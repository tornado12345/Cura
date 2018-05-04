// Copyright (c) 2018 Ultimaker B.V.
// Toolbox is released under the terms of the LGPLv3 or higher.

import QtQuick 2.3
import QtQuick.Controls 1.4
import QtQuick.Controls.Styles 1.4
import QtQuick.Layouts 1.3
import UM 1.1 as UM

Item
{
    height: childrenRect.height
    Layout.alignment: Qt.AlignTop | Qt.AlignLeft
    Rectangle
    {
        id: highlight
        anchors.fill: parent
        opacity: 0.0
        color: UM.Theme.getColor("primary")
    }
    Row
    {
        width: parent.width
        height: childrenRect.height
        spacing: Math.floor(UM.Theme.getSize("narrow_margin").width)
        Rectangle
        {
            id: thumbnail
            width: UM.Theme.getSize("toolbox_thumbnail_small").width
            height: UM.Theme.getSize("toolbox_thumbnail_small").height
            color: "white"
            border.width: UM.Theme.getSize("default_lining").width
            border.color: UM.Theme.getColor("lining")
            Image
            {
                anchors.centerIn: parent
                width: UM.Theme.getSize("toolbox_thumbnail_small").width - UM.Theme.getSize("wide_margin").width
                height: UM.Theme.getSize("toolbox_thumbnail_small").height - UM.Theme.getSize("wide_margin").width
                fillMode: Image.PreserveAspectFit
                source: model.icon_url || "../images/logobot.svg"
                mipmap: true
            }
        }
        Column
        {
            width: parent.width - thumbnail.width - parent.spacing
            spacing: Math.floor(UM.Theme.getSize("narrow_margin").width)
            Label
            {
                id: name
                text: model.name
                width: parent.width
                wrapMode: Text.WordWrap
                color: UM.Theme.getColor("text")
                font: UM.Theme.getFont("default_bold")
            }
            Label
            {
                id: info
                text: model.description
                maximumLineCount: 2
                elide: Text.ElideRight
                width: parent.width
                wrapMode: Text.WordWrap
                color: UM.Theme.getColor("text_medium")
                font: UM.Theme.getFont("very_small")
            }
        }
    }
    MouseArea
    {
        anchors.fill: parent
        hoverEnabled: true
        onEntered:
        {
            thumbnail.border.color = UM.Theme.getColor("primary")
            highlight.opacity = 0.1
        }
        onExited:
        {
            thumbnail.border.color = UM.Theme.getColor("lining")
            highlight.opacity = 0.0
        }
        onClicked:
        {
            base.selection = model
            switch(toolbox.viewCategory)
            {
                case "material":
                    toolbox.viewPage = "author"
                    toolbox.filterModelByProp("packages", "author_id", model.id)
                    break
                default:
                    toolbox.viewPage = "detail"
                    toolbox.filterModelByProp("packages", "id", model.id)
                    break
            }
        }
    }
}
