# Copyright (c) 2018 Ultimaker B.V.
# Toolbox is released under the terms of the LGPLv3 or higher.

import json
import os
import tempfile
import platform
from typing import cast, Any, Dict, List, Set, TYPE_CHECKING, Tuple, Optional, Union

from PyQt5.QtCore import QUrl, QObject, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

from UM.Logger import Logger
from UM.PluginRegistry import PluginRegistry
from UM.Extension import Extension
from UM.i18n import i18nCatalog
from UM.Version import Version

from cura import ApplicationMetadata
from cura import UltimakerCloudAuthentication
from cura.CuraApplication import CuraApplication

from .AuthorsModel import AuthorsModel
from .PackagesModel import PackagesModel

if TYPE_CHECKING:
    from cura.Settings.GlobalStack import GlobalStack

i18n_catalog = i18nCatalog("cura")


##  The Toolbox class is responsible of communicating with the server through the API
class Toolbox(QObject, Extension):
    def __init__(self, application: CuraApplication) -> None:
        super().__init__()

        self._application = application  # type: CuraApplication

        self._sdk_version = ApplicationMetadata.CuraSDKVersion  # type: Union[str, int]
        self._cloud_api_version = UltimakerCloudAuthentication.CuraCloudAPIVersion  # type: str
        self._cloud_api_root = UltimakerCloudAuthentication.CuraCloudAPIRoot  # type: str
        self._api_url = None  # type: Optional[str]

        # Network:
        self._download_request = None  # type: Optional[QNetworkRequest]
        self._download_reply = None  # type: Optional[QNetworkReply]
        self._download_progress = 0  # type: float
        self._is_downloading = False  # type: bool
        self._network_manager = None  # type: Optional[QNetworkAccessManager]
        self._request_headers = [] # type: List[Tuple[bytes, bytes]]
        self._updateRequestHeader()


        self._request_urls = {}  # type: Dict[str, QUrl]
        self._to_update = []  # type: List[str] # Package_ids that are waiting to be updated
        self._old_plugin_ids = set()  # type: Set[str]
        self._old_plugin_metadata = dict()  # type: Dict[str, Dict[str, Any]]

        # The responses as given by the server parsed to a list.
        self._server_response_data = {
            "authors":             [],
            "packages":            []
        }  # type: Dict[str, List[Any]]

        # Models:
        self._models = {
            "authors":             AuthorsModel(self),
            "packages":            PackagesModel(self),
        }  # type: Dict[str, Union[AuthorsModel, PackagesModel]]

        self._plugins_showcase_model = PackagesModel(self)
        self._plugins_available_model = PackagesModel(self)
        self._plugins_installed_model = PackagesModel(self)

        self._materials_showcase_model = AuthorsModel(self)
        self._materials_available_model = AuthorsModel(self)
        self._materials_installed_model = PackagesModel(self)
        self._materials_generic_model = PackagesModel(self)

        # These properties are for keeping track of the UI state:
        # ----------------------------------------------------------------------
        # View category defines which filter to use, and therefore effectively
        # which category is currently being displayed. For example, possible
        # values include "plugin" or "material", but also "installed".
        self._view_category = "plugin"  # type: str

        # View page defines which type of page layout to use. For example,
        # possible values include "overview", "detail" or "author".
        self._view_page = "loading"  # type: str

        # Active package refers to which package is currently being downloaded,
        # installed, or otherwise modified.
        self._active_package = None  # type: Optional[Dict[str, Any]]

        self._dialog = None  # type: Optional[QObject]
        self._confirm_reset_dialog = None  # type: Optional[QObject]
        self._resetUninstallVariables()

        self._restart_required = False  # type: bool

        # variables for the license agreement dialog
        self._license_dialog_plugin_name = ""  # type: str
        self._license_dialog_license_content = ""  # type: str
        self._license_dialog_plugin_file_location = ""  # type: str
        self._restart_dialog_message = ""  # type: str

        self._application.initializationFinished.connect(self._onAppInitialized)
        self._application.getCuraAPI().account.loginStateChanged.connect(self._updateRequestHeader)

    # Signals:
    # --------------------------------------------------------------------------
    # Downloading changes
    activePackageChanged = pyqtSignal()
    onDownloadProgressChanged = pyqtSignal()
    onIsDownloadingChanged = pyqtSignal()
    restartRequiredChanged = pyqtSignal()
    installChanged = pyqtSignal()
    enabledChanged = pyqtSignal()

    # UI changes
    viewChanged = pyqtSignal()
    detailViewChanged = pyqtSignal()
    filterChanged = pyqtSignal()
    metadataChanged = pyqtSignal()
    showLicenseDialog = pyqtSignal()
    uninstallVariablesChanged = pyqtSignal()

    def _updateRequestHeader(self):
        self._request_headers = [
            (b"User-Agent",
            str.encode(
                "%s/%s (%s %s)" % (
                    self._application.getApplicationName(),
                    self._application.getVersion(),
                    platform.system(),
                    platform.machine(),
                )
            ))
        ]
        access_token = self._application.getCuraAPI().account.accessToken
        if access_token:
            self._request_headers.append((b"Authorization", "Bearer {}".format(access_token).encode()))

    def _resetUninstallVariables(self) -> None:
        self._package_id_to_uninstall = None  # type: Optional[str]
        self._package_name_to_uninstall = ""
        self._package_used_materials = []  # type: List[Tuple[GlobalStack, str, str]]
        self._package_used_qualities = []  # type: List[Tuple[GlobalStack, str, str]]

    @pyqtSlot(str, int)
    def ratePackage(self, package_id: str, rating: int) -> None:
        url = QUrl("{base_url}/packages/{package_id}/ratings".format(base_url=self._api_url, package_id = package_id))

        self._rate_request = QNetworkRequest(url)
        for header_name, header_value in self._request_headers:
            cast(QNetworkRequest, self._rate_request).setRawHeader(header_name, header_value)
        data = "{\"data\": {\"cura_version\": \"%s\", \"rating\": %i}}" % (Version(self._application.getVersion()), rating)
        self._rate_reply = cast(QNetworkAccessManager, self._network_manager).put(self._rate_request, data.encode())

    @pyqtSlot(result = str)
    def getLicenseDialogPluginName(self) -> str:
        return self._license_dialog_plugin_name

    @pyqtSlot(result = str)
    def getLicenseDialogPluginFileLocation(self) -> str:
        return self._license_dialog_plugin_file_location

    @pyqtSlot(result = str)
    def getLicenseDialogLicenseContent(self) -> str:
        return self._license_dialog_license_content

    def openLicenseDialog(self, plugin_name: str, license_content: str, plugin_file_location: str) -> None:
        self._license_dialog_plugin_name = plugin_name
        self._license_dialog_license_content = license_content
        self._license_dialog_plugin_file_location = plugin_file_location
        self.showLicenseDialog.emit()

    # This is a plugin, so most of the components required are not ready when
    # this is initialized. Therefore, we wait until the application is ready.
    def _onAppInitialized(self) -> None:
        self._plugin_registry = self._application.getPluginRegistry()
        self._package_manager = self._application.getPackageManager()
        self._api_url = "{cloud_api_root}/cura-packages/v{cloud_api_version}/cura/v{sdk_version}".format(
            cloud_api_root = self._cloud_api_root,
            cloud_api_version = self._cloud_api_version,
            sdk_version = self._sdk_version
        )
        self._request_urls = {
            "authors": QUrl("{base_url}/authors".format(base_url = self._api_url)),
            "packages": QUrl("{base_url}/packages".format(base_url = self._api_url))
        }

    @pyqtSlot()
    def browsePackages(self) -> None:
        # Create the network manager:
        # This was formerly its own function but really had no reason to be as
        # it was never called more than once ever.
        if self._network_manager is not None:
            self._network_manager.finished.disconnect(self._onRequestFinished)
            self._network_manager.networkAccessibleChanged.disconnect(self._onNetworkAccessibleChanged)
        self._network_manager = QNetworkAccessManager()
        self._network_manager.finished.connect(self._onRequestFinished)
        self._network_manager.networkAccessibleChanged.connect(self._onNetworkAccessibleChanged)

        # Make remote requests:
        self._makeRequestByType("packages")
        self._makeRequestByType("authors")

        # Gather installed packages:
        self._updateInstalledModels()

        if not self._dialog:
            self._dialog = self._createDialog("Toolbox.qml")

        if not self._dialog:
            Logger.log("e", "Unexpected error trying to create the 'Marketplace' dialog.")
            return

        self._dialog.show()

        # Apply enabled/disabled state to installed plugins
        self.enabledChanged.emit()

    def _createDialog(self, qml_name: str) -> Optional[QObject]:
        Logger.log("d", "Marketplace: Creating dialog [%s].", qml_name)
        plugin_path = PluginRegistry.getInstance().getPluginPath(self.getPluginId())
        if not plugin_path:
            return None
        path = os.path.join(plugin_path, "resources", "qml", qml_name)
        
        dialog = self._application.createQmlComponent(path, {"toolbox": self})
        if not dialog:
            raise Exception("Failed to create Marketplace dialog")
        return dialog

    def _convertPluginMetadata(self, plugin_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            highest_sdk_version_supported = Version(0)
            for supported_version in plugin_data["plugin"]["supported_sdk_versions"]:
                if supported_version > highest_sdk_version_supported:
                    highest_sdk_version_supported = supported_version

            formatted = {
                "package_id": plugin_data["id"],
                "package_type": "plugin",
                "display_name": plugin_data["plugin"]["name"],
                "package_version": plugin_data["plugin"]["version"],
                "sdk_version": highest_sdk_version_supported,
                "author": {
                    "author_id": plugin_data["plugin"]["author"],
                    "display_name": plugin_data["plugin"]["author"]
                },
                "is_installed": True,
                "description": plugin_data["plugin"]["description"]
            }
            return formatted
        except KeyError:
            Logger.log("w", "Unable to convert plugin meta data %s", str(plugin_data))
            return None

    @pyqtSlot()
    def _updateInstalledModels(self) -> None:
        # This is moved here to avoid code duplication and so that after installing plugins they get removed from the
        # list of old plugins
        old_plugin_ids = self._plugin_registry.getInstalledPlugins()
        installed_package_ids = self._package_manager.getAllInstalledPackageIDs()
        scheduled_to_remove_package_ids = self._package_manager.getToRemovePackageIDs()

        self._old_plugin_ids = set()
        self._old_plugin_metadata = dict()

        for plugin_id in old_plugin_ids:
            # Neither the installed packages nor the packages that are scheduled to remove are old plugins
            if plugin_id not in installed_package_ids and plugin_id not in scheduled_to_remove_package_ids:
                Logger.log("i", "Found a plugin that was installed with the old plugin browser: %s", plugin_id)

                old_metadata = self._plugin_registry.getMetaData(plugin_id)
                new_metadata = self._convertPluginMetadata(old_metadata)
                if new_metadata is None:
                    # Something went wrong converting it.
                    continue
                self._old_plugin_ids.add(plugin_id)
                self._old_plugin_metadata[new_metadata["package_id"]] = new_metadata

        all_packages = self._package_manager.getAllInstalledPackagesInfo()
        if "plugin" in all_packages:
            # For old plugins, we only want to include the old custom plugin that were installed via the old toolbox.
            # The bundled plugins will be included in JSON files in the "bundled_packages" folder, so the bundled
            # plugins should be excluded from the old plugins list/dict.
            all_plugin_package_ids = set(package["package_id"] for package in all_packages["plugin"])
            self._old_plugin_ids = set(plugin_id for plugin_id in self._old_plugin_ids
                                    if plugin_id not in all_plugin_package_ids)
            self._old_plugin_metadata = {k: v for k, v in self._old_plugin_metadata.items() if k in self._old_plugin_ids}

            self._plugins_installed_model.setMetadata(all_packages["plugin"] + list(self._old_plugin_metadata.values()))
            self.metadataChanged.emit()
        if "material" in all_packages:
            self._materials_installed_model.setMetadata(all_packages["material"])
            self.metadataChanged.emit()

    @pyqtSlot(str)
    def install(self, file_path: str) -> None:
        self._package_manager.installPackage(file_path)
        self.installChanged.emit()
        self._updateInstalledModels()
        self.metadataChanged.emit()
        self._restart_required = True
        self.restartRequiredChanged.emit()

    ##  Check package usage and uninstall
    #   If the package is in use, you'll get a confirmation dialog to set everything to default
    @pyqtSlot(str)
    def checkPackageUsageAndUninstall(self, package_id: str) -> None:
        package_used_materials, package_used_qualities = self._package_manager.getMachinesUsingPackage(package_id)
        if package_used_materials or package_used_qualities:
            # Set up "uninstall variables" for resetMaterialsQualitiesAndUninstall
            self._package_id_to_uninstall = package_id
            package_info = self._package_manager.getInstalledPackageInfo(package_id)
            self._package_name_to_uninstall = package_info.get("display_name", package_info.get("package_id"))
            self._package_used_materials = package_used_materials
            self._package_used_qualities = package_used_qualities
            # Ask change to default material / profile
            if self._confirm_reset_dialog is None:
                self._confirm_reset_dialog = self._createDialog("ToolboxConfirmUninstallResetDialog.qml")
            self.uninstallVariablesChanged.emit()
            if self._confirm_reset_dialog is None:
                Logger.log("e", "ToolboxConfirmUninstallResetDialog should have been initialized, but it is not. Not showing dialog and not uninstalling package.")
            else:
                self._confirm_reset_dialog.show()
        else:
            # Plain uninstall
            self.uninstall(package_id)

    @pyqtProperty(str, notify = uninstallVariablesChanged)
    def pluginToUninstall(self) -> str:
        return self._package_name_to_uninstall

    @pyqtProperty(str, notify = uninstallVariablesChanged)
    def uninstallUsedMaterials(self) -> str:
        return "\n".join(["%s (%s)" % (str(global_stack.getName()), material) for global_stack, extruder_nr, material in self._package_used_materials])

    @pyqtProperty(str, notify = uninstallVariablesChanged)
    def uninstallUsedQualities(self) -> str:
        return "\n".join(["%s (%s)" % (str(global_stack.getName()), quality) for global_stack, extruder_nr, quality in self._package_used_qualities])

    @pyqtSlot()
    def closeConfirmResetDialog(self) -> None:
        if self._confirm_reset_dialog is not None:
            self._confirm_reset_dialog.close()

    ##  Uses "uninstall variables" to reset qualities and materials, then uninstall
    #   It's used as an action on Confirm reset on Uninstall
    @pyqtSlot()
    def resetMaterialsQualitiesAndUninstall(self) -> None:
        application = CuraApplication.getInstance()
        material_manager = application.getMaterialManager()
        quality_manager = application.getQualityManager()
        machine_manager = application.getMachineManager()

        for global_stack, extruder_nr, container_id in self._package_used_materials:
            default_material_node = material_manager.getDefaultMaterial(global_stack, extruder_nr, global_stack.extruders[extruder_nr].variant.getName())
            machine_manager.setMaterial(extruder_nr, default_material_node, global_stack = global_stack)
        for global_stack, extruder_nr, container_id in self._package_used_qualities:
            default_quality_group = quality_manager.getDefaultQualityType(global_stack)
            machine_manager.setQualityGroup(default_quality_group, global_stack = global_stack)

        if self._package_id_to_uninstall is not None:
            self._markPackageMaterialsAsToBeUninstalled(self._package_id_to_uninstall)
            self.uninstall(self._package_id_to_uninstall)
        self._resetUninstallVariables()
        self.closeConfirmResetDialog()

    def _markPackageMaterialsAsToBeUninstalled(self, package_id: str) -> None:
        container_registry = self._application.getContainerRegistry()

        all_containers = self._package_manager.getPackageContainerIds(package_id)
        for container_id in all_containers:
            containers = container_registry.findInstanceContainers(id = container_id)
            if not containers:
                continue
            container = containers[0]
            if container.getMetaDataEntry("type") != "material":
                continue
            root_material_id = container.getMetaDataEntry("base_file")
            root_material_containers = container_registry.findInstanceContainers(id = root_material_id)
            if not root_material_containers:
                continue
            root_material_container = root_material_containers[0]
            root_material_container.setMetaDataEntry("removed", True)

    @pyqtSlot(str)
    def uninstall(self, package_id: str) -> None:
        self._package_manager.removePackage(package_id, force_add = True)
        self.installChanged.emit()
        self._updateInstalledModels()
        self.metadataChanged.emit()
        self._restart_required = True
        self.restartRequiredChanged.emit()

    ##  Actual update packages that are in self._to_update
    def _update(self) -> None:
        if self._to_update:
            plugin_id = self._to_update.pop(0)
            remote_package = self.getRemotePackage(plugin_id)
            if remote_package:
                download_url = remote_package["download_url"]
                Logger.log("d", "Updating package [%s]..." % plugin_id)
                self.startDownload(download_url)
            else:
                Logger.log("e", "Could not update package [%s] because there is no remote package info available.", plugin_id)

        if self._to_update:
            self._application.callLater(self._update)

    ##  Update a plugin by plugin_id
    @pyqtSlot(str)
    def update(self, plugin_id: str) -> None:
        self._to_update.append(plugin_id)
        self._application.callLater(self._update)

    @pyqtSlot(str)
    def enable(self, plugin_id: str) -> None:
        self._plugin_registry.enablePlugin(plugin_id)
        self.enabledChanged.emit()
        Logger.log("i", "%s was set as 'active'.", plugin_id)
        self._restart_required = True
        self.restartRequiredChanged.emit()

    @pyqtSlot(str)
    def disable(self, plugin_id: str) -> None:
        self._plugin_registry.disablePlugin(plugin_id)
        self.enabledChanged.emit()
        Logger.log("i", "%s was set as 'deactive'.", plugin_id)
        self._restart_required = True
        self.restartRequiredChanged.emit()

    @pyqtProperty(bool, notify = metadataChanged)
    def dataReady(self) -> bool:
        return self._packages_model is not None

    @pyqtProperty(bool, notify = restartRequiredChanged)
    def restartRequired(self) -> bool:
        return self._restart_required

    @pyqtSlot()
    def restart(self) -> None:
        self._application.windowClosed()

    def getRemotePackage(self, package_id: str) -> Optional[Dict]:
        # TODO: make the lookup in a dict, not a loop. canUpdate is called for every item.
        remote_package = None
        for package in self._server_response_data["packages"]:
            if package["package_id"] == package_id:
                remote_package = package
                break
        return remote_package

    # Checks
    # --------------------------------------------------------------------------
    @pyqtSlot(str, result = bool)
    def canUpdate(self, package_id: str) -> bool:
        local_package = self._package_manager.getInstalledPackageInfo(package_id)
        if local_package is None:
            local_package = self.getOldPluginPackageMetadata(package_id)
            if local_package is None:
                return False

        remote_package = self.getRemotePackage(package_id)
        if remote_package is None:
            return False

        local_version = Version(local_package["package_version"])
        remote_version = Version(remote_package["package_version"])
        can_upgrade = False
        if remote_version > local_version:
            can_upgrade = True
        # A package with the same version can be built to have different SDK versions. So, for a package with the same
        # version, we also need to check if the current one has a lower SDK version. If so, this package should also
        # be upgradable.
        elif remote_version == local_version:
            # First read sdk_version_semver. If that doesn't exist, read just sdk_version (old version system).
            remote_sdk_version = Version(remote_package.get("sdk_version_semver", remote_package.get("sdk_version", 0)))
            local_sdk_version = Version(local_package.get("sdk_version_semver", local_package.get("sdk_version", 0)))
            can_upgrade = local_sdk_version < remote_sdk_version

        return can_upgrade

    @pyqtSlot(str, result = bool)
    def canDowngrade(self, package_id: str) -> bool:
        # If the currently installed version is higher than the bundled version (if present), the we can downgrade
        # this package.
        local_package = self._package_manager.getInstalledPackageInfo(package_id)
        if local_package is None:
            return False

        bundled_package = self._package_manager.getBundledPackageInfo(package_id)
        if bundled_package is None:
            return False

        local_version = Version(local_package["package_version"])
        bundled_version = Version(bundled_package["package_version"])
        return bundled_version < local_version

    @pyqtSlot(str, result = bool)
    def isInstalled(self, package_id: str) -> bool:
        result = self._package_manager.isPackageInstalled(package_id)
        # Also check the old plugins list if it's not found in the package manager.
        if not result:
            result = self.isOldPlugin(package_id)
        return result

    @pyqtSlot(str, result = int)
    def getNumberOfInstalledPackagesByAuthor(self, author_id: str) -> int:
        count = 0
        for package in self._materials_installed_model.items:
            if package["author_id"] == author_id:
                count += 1
        return count

    # This slot is only used to get the number of material packages by author, not any other type of packages.
    @pyqtSlot(str, result = int)
    def getTotalNumberOfMaterialPackagesByAuthor(self, author_id: str) -> int:
        count = 0
        for package in self._server_response_data["packages"]:
            if package["package_type"] == "material":
                if package["author"]["author_id"] == author_id:
                    count += 1
        return count

    @pyqtSlot(str, result = bool)
    def isEnabled(self, package_id: str) -> bool:
        if package_id in self._plugin_registry.getActivePlugins():
            return True
        return False

    # Check for plugins that were installed with the old plugin browser
    def isOldPlugin(self, plugin_id: str) -> bool:
        return plugin_id in self._old_plugin_ids

    def getOldPluginPackageMetadata(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        return self._old_plugin_metadata.get(plugin_id)

    def isLoadingComplete(self) -> bool:
        populated = 0
        for metadata_list in self._server_response_data.items():
            if metadata_list:
                populated += 1
        return populated == len(self._server_response_data.items())

    # Make API Calls
    # --------------------------------------------------------------------------
    def _makeRequestByType(self, request_type: str) -> None:
        Logger.log("i", "Requesting %s metadata from server.", request_type)
        request = QNetworkRequest(self._request_urls[request_type])
        for header_name, header_value in self._request_headers:
            request.setRawHeader(header_name, header_value)
        if self._network_manager:
            self._network_manager.get(request)

    @pyqtSlot(str)
    def startDownload(self, url: str) -> None:
        Logger.log("i", "Attempting to download & install package from %s.", url)
        url = QUrl(url)
        self._download_request = QNetworkRequest(url)
        if hasattr(QNetworkRequest, "FollowRedirectsAttribute"):
            # Patch for Qt 5.6-5.8
            cast(QNetworkRequest, self._download_request).setAttribute(QNetworkRequest.FollowRedirectsAttribute, True)
        if hasattr(QNetworkRequest, "RedirectPolicyAttribute"):
            # Patch for Qt 5.9+
            cast(QNetworkRequest, self._download_request).setAttribute(QNetworkRequest.RedirectPolicyAttribute, True)
        for header_name, header_value in self._request_headers:
            cast(QNetworkRequest, self._download_request).setRawHeader(header_name, header_value)
        self._download_reply = cast(QNetworkAccessManager, self._network_manager).get(self._download_request)
        self.setDownloadProgress(0)
        self.setIsDownloading(True)
        cast(QNetworkReply, self._download_reply).downloadProgress.connect(self._onDownloadProgress)

    @pyqtSlot()
    def cancelDownload(self) -> None:
        Logger.log("i", "User cancelled the download of a package.")
        self.resetDownload()

    def resetDownload(self) -> None:
        if self._download_reply:
            try:
                self._download_reply.downloadProgress.disconnect(self._onDownloadProgress)
            except TypeError:  # Raised when the method is not connected to the signal yet.
                pass  # Don't need to disconnect.
            self._download_reply.abort()
        self._download_reply = None
        self._download_request = None
        self.setDownloadProgress(0)
        self.setIsDownloading(False)

    # Handlers for Network Events
    # --------------------------------------------------------------------------
    def _onNetworkAccessibleChanged(self, network_accessibility: QNetworkAccessManager.NetworkAccessibility) -> None:
        if network_accessibility == QNetworkAccessManager.NotAccessible:
            self.resetDownload()

    def _onRequestFinished(self, reply: QNetworkReply) -> None:
        if reply.error() == QNetworkReply.TimeoutError:
            Logger.log("w", "Got a timeout.")
            self.setViewPage("errored")
            self.resetDownload()
            return

        if reply.error() == QNetworkReply.HostNotFoundError:
            Logger.log("w", "Unable to reach server.")
            self.setViewPage("errored")
            self.resetDownload()
            return

        if reply.operation() == QNetworkAccessManager.GetOperation:
            for response_type, url in self._request_urls.items():
                if reply.url() == url:
                    if reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) == 200:
                        try:
                            json_data = json.loads(bytes(reply.readAll()).decode("utf-8"))

                            # Check for errors:
                            if "errors" in json_data:
                                for error in json_data["errors"]:
                                    Logger.log("e", "%s", error["title"])
                                return

                            # Create model and apply metadata:
                            if not self._models[response_type]:
                                Logger.log("e", "Could not find the %s model.", response_type)
                                break
                            
                            self._server_response_data[response_type] = json_data["data"]
                            self._models[response_type].setMetadata(self._server_response_data[response_type])

                            if response_type is "packages":
                                self._models[response_type].setFilter({"type": "plugin"})
                                self.reBuildMaterialsModels()
                                self.reBuildPluginsModels()
                            elif response_type is "authors":
                                self._models[response_type].setFilter({"package_types": "material"})
                                self._models[response_type].setFilter({"tags": "generic"})

                            self.metadataChanged.emit()

                            if self.isLoadingComplete():
                                self.setViewPage("overview")

                        except json.decoder.JSONDecodeError:
                            Logger.log("w", "Received invalid JSON for %s.", response_type)
                            break
                    else:
                        Logger.log("w", "Unable to connect with the server, we got a response code %s while trying to connect to %s", reply.attribute(QNetworkRequest.HttpStatusCodeAttribute), reply.url())
                        self.setViewPage("errored")
                        self.resetDownload()
        elif reply.operation() == QNetworkAccessManager.PutOperation:
            # Ignore any operation that is not a get operation
            pass

    def _onDownloadProgress(self, bytes_sent: int, bytes_total: int) -> None:
        if bytes_total > 0:
            new_progress = bytes_sent / bytes_total * 100
            self.setDownloadProgress(new_progress)
            if bytes_sent == bytes_total:
                self.setIsDownloading(False)
                self._download_reply = cast(QNetworkReply, self._download_reply)
                self._download_reply.downloadProgress.disconnect(self._onDownloadProgress)
                
                # Check if the download was sucessfull
                if self._download_reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) != 200:
                    Logger.log("w", "Failed to download package. The following error was returned: %s", json.loads(bytes(self._download_reply.readAll()).decode("utf-8")))
                    return
                # Must not delete the temporary file on Windows
                self._temp_plugin_file = tempfile.NamedTemporaryFile(mode = "w+b", suffix = ".curapackage", delete = False)
                file_path = self._temp_plugin_file.name
                # Write first and close, otherwise on Windows, it cannot read the file
                self._temp_plugin_file.write(cast(QNetworkReply, self._download_reply).readAll())
                self._temp_plugin_file.close()
                self._onDownloadComplete(file_path)

    def _onDownloadComplete(self, file_path: str) -> None:
        Logger.log("i", "Download complete.")
        package_info = self._package_manager.getPackageInfo(file_path)
        if not package_info:
            Logger.log("w", "Package file [%s] was not a valid CuraPackage.", file_path)
            return

        license_content = self._package_manager.getPackageLicense(file_path)
        if license_content is not None:
            self.openLicenseDialog(package_info["package_id"], license_content, file_path)
            return

        self.install(file_path)

    # Getter & Setters for Properties:
    # --------------------------------------------------------------------------
    def setDownloadProgress(self, progress: float) -> None:
        if progress != self._download_progress:
            self._download_progress = progress
            self.onDownloadProgressChanged.emit()

    @pyqtProperty(int, fset = setDownloadProgress, notify = onDownloadProgressChanged)
    def downloadProgress(self) -> float:
        return self._download_progress

    def setIsDownloading(self, is_downloading: bool) -> None:
        if self._is_downloading != is_downloading:
            self._is_downloading = is_downloading
            self.onIsDownloadingChanged.emit()

    @pyqtProperty(bool, fset = setIsDownloading, notify = onIsDownloadingChanged)
    def isDownloading(self) -> bool:
        return self._is_downloading

    def setActivePackage(self, package: Dict[str, Any]) -> None:
        if self._active_package != package:
            self._active_package = package
            self.activePackageChanged.emit()

    ##  The active package is the package that is currently being downloaded
    @pyqtProperty(QObject, fset = setActivePackage, notify = activePackageChanged)
    def activePackage(self) -> Optional[Dict[str, Any]]:
        return self._active_package

    def setViewCategory(self, category: str = "plugin") -> None:
        if self._view_category != category:
            self._view_category = category
            self.viewChanged.emit()

    @pyqtProperty(str, fset = setViewCategory, notify = viewChanged)
    def viewCategory(self) -> str:
        return self._view_category

    def setViewPage(self, page: str = "overview") -> None:
        if self._view_page != page:
            self._view_page = page
            self.viewChanged.emit()

    @pyqtProperty(str, fset = setViewPage, notify = viewChanged)
    def viewPage(self) -> str:
        return self._view_page

    # Exposed Models:
    # --------------------------------------------------------------------------
    @pyqtProperty(QObject, constant=True)
    def authorsModel(self) -> AuthorsModel:
        return cast(AuthorsModel, self._models["authors"])

    @pyqtProperty(QObject, constant=True)
    def packagesModel(self) -> PackagesModel:
        return cast(PackagesModel, self._models["packages"])

    @pyqtProperty(QObject, constant=True)
    def pluginsShowcaseModel(self) -> PackagesModel:
        return self._plugins_showcase_model

    @pyqtProperty(QObject, constant=True)
    def pluginsAvailableModel(self) -> PackagesModel:
        return self._plugins_available_model

    @pyqtProperty(QObject, constant=True)
    def pluginsInstalledModel(self) -> PackagesModel:
        return self._plugins_installed_model

    @pyqtProperty(QObject, constant=True)
    def materialsShowcaseModel(self) -> AuthorsModel:
        return self._materials_showcase_model

    @pyqtProperty(QObject, constant=True)
    def materialsAvailableModel(self) -> AuthorsModel:
        return self._materials_available_model

    @pyqtProperty(QObject, constant=True)
    def materialsInstalledModel(self) -> PackagesModel:
        return self._materials_installed_model

    @pyqtProperty(QObject, constant=True)
    def materialsGenericModel(self) -> PackagesModel:
        return self._materials_generic_model

    # Filter Models:
    # --------------------------------------------------------------------------
    @pyqtSlot(str, str, str)
    def filterModelByProp(self, model_type: str, filter_type: str, parameter: str) -> None:
        if not self._models[model_type]:
            Logger.log("w", "Couldn't filter %s model because it doesn't exist.", model_type)
            return
        self._models[model_type].setFilter({filter_type: parameter})
        self.filterChanged.emit()

    @pyqtSlot(str, "QVariantMap")
    def setFilters(self, model_type: str, filter_dict: dict) -> None:
        if not self._models[model_type]:
            Logger.log("w", "Couldn't filter %s model because it doesn't exist.", model_type)
            return
        self._models[model_type].setFilter(filter_dict)
        self.filterChanged.emit()

    @pyqtSlot(str)
    def removeFilters(self, model_type: str) -> None:
        if not self._models[model_type]:
            Logger.log("w", "Couldn't remove filters on %s model because it doesn't exist.", model_type)
            return
        self._models[model_type].setFilter({})
        self.filterChanged.emit()

    # HACK(S):
    # --------------------------------------------------------------------------
    def reBuildMaterialsModels(self) -> None:
        materials_showcase_metadata = []
        materials_available_metadata = []
        materials_generic_metadata = []

        processed_authors = []  # type: List[str]

        for item in self._server_response_data["packages"]:
            if item["package_type"] == "material":

                author = item["author"]
                if author["author_id"] in processed_authors:
                    continue

                # Generic materials to be in the same section
                if "generic" in item["tags"]:
                    materials_generic_metadata.append(item)
                else:
                    if "showcase" in item["tags"]:
                        materials_showcase_metadata.append(author)
                    else:
                        materials_available_metadata.append(author)

                    processed_authors.append(author["author_id"])

        self._materials_showcase_model.setMetadata(materials_showcase_metadata)
        self._materials_available_model.setMetadata(materials_available_metadata)
        self._materials_generic_model.setMetadata(materials_generic_metadata)

    def reBuildPluginsModels(self) -> None:
        plugins_showcase_metadata = []
        plugins_available_metadata = []

        for item in self._server_response_data["packages"]:
            if item["package_type"] == "plugin":
                if "showcase" in item["tags"]:
                    plugins_showcase_metadata.append(item)
                else:
                    plugins_available_metadata.append(item)

        self._plugins_showcase_model.setMetadata(plugins_showcase_metadata)
        self._plugins_available_model.setMetadata(plugins_available_metadata)
