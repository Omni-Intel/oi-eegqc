import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

QuietPopup {
    id: sheet
    objectName: "channelSheet"
    required property var backend
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(440, Overlay.overlay.width - 48)
    modal: true
    focus: true
    visible: backend.channelsOpen
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    onAboutToHide: if (backend.channelsOpen) backend.cancelChannels()
    contentItem: ColumnLayout {
        spacing: 12
        Text { text: "选择通道"; color: "#303941"; font.pixelSize: 15 }
        Text {
            text: backend.channelCount === 0 ? "未能读取待评文件的通道。" : "选择待评文件的通道"
            color: "#7c8791"; wrapMode: Text.Wrap; Layout.fillWidth: true
        }
        Rectangle {
            visible: backend.channelCount > 0
            Layout.fillWidth: true; Layout.preferredHeight: Math.max(44, Math.min(320, backend.channelCount * 44 + 2, Overlay.overlay.height - 220))
            radius: 8; color: "#f7f8fa"; border.color: "#e5e8eb"
            ListView {
                id: list
                objectName: "channelList"
                anchors.fill: parent; anchors.margins: 1; clip: true
                model: sheet.backend.channelModel
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded; width: 7 }
                delegate: Rectangle {
                    id: line
                    required property int index
                    required property string name
                    required property int row
                    required property bool chosen
                    width: ListView.view.width; height: 44
                    color: mouse.containsMouse ? "#eef2f5" : "transparent"
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 14; spacing: 10
                        Rectangle {
                            width: 18; height: 18; radius: 4
                            color: line.chosen ? "#272e35" : "#ffffff"
                            border.color: line.chosen ? "#272e35" : "#cfd6dc"
                            Text { anchors.centerIn: parent; text: line.chosen ? "✓" : ""; color: "white"; font.pixelSize: 12 }
                        }
                        Text { text: line.name; textFormat: Text.PlainText; color: "#303941"; Layout.fillWidth: true; elide: Text.ElideRight }
                        Text { text: String(line.row); color: "#99a2ab"; Layout.preferredWidth: 28; horizontalAlignment: Text.AlignRight }
                    }
                    MouseArea {
                        id: mouse; anchors.fill: parent; hoverEnabled: true
                        onClicked: sheet.backend.toggleChannel(line.index)
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            Text {
                visible: backend.channelCount > 0
                text: "已选 " + backend.selectedChannelCount + " / " + backend.channelCount
                color: "#7c8791"; Layout.fillWidth: true
            }
            Item { Layout.fillWidth: true; visible: backend.channelCount === 0 }
            QuietButton { text: "全选"; visible: backend.channelCount > 0; onClicked: backend.selectAllChannels() }
            QuietButton { text: "清空"; visible: backend.channelCount > 0; onClicked: backend.clearChannels() }
        }
        RowLayout {
            Layout.alignment: Qt.AlignRight; spacing: 8
            QuietButton { text: "取消"; onClicked: backend.cancelChannels() }
            QuietButton {
                objectName: "acceptChannels"
                text: "确定"; primary: true
                enabled: backend.selectedChannelCount > 0
                onClicked: backend.acceptChannels()
            }
        }
    }
}
