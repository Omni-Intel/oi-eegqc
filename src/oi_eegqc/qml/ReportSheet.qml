import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

QuietPopup {
    id: sheet
    objectName: "reportSheet"
    required property var backend
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(480, Overlay.overlay.width - 48)
    height: Math.min(560, Overlay.overlay.height - 48)
    modal: true
    focus: true
    visible: backend.reportOpen
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    onAboutToHide: if (backend.reportOpen) backend.closeReport()
    contentItem: ColumnLayout {
        spacing: 12
        Text {
            text: backend.reportCard.name || "评分详情"
            textFormat: Text.PlainText
            color: "#303941"; font.pixelSize: 15
            elide: Text.ElideMiddle; Layout.fillWidth: true
        }
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            ColumnLayout {
                width: parent.width
                spacing: 12
                RowLayout {
                    spacing: 10
                    Text {
                        visible: backend.reportCard.score !== ""
                        text: backend.reportCard.score
                        color: "#303941"; font.pixelSize: 28
                    }
                    Text {
                        text: backend.reportCard.headline || ""
                        color: "#7c8791"; wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }
                }
                Text {
                    visible: (backend.reportCard.layout || "") !== ""
                    text: backend.reportCard.layout || ""
                    color: "#99a2ab"; Layout.fillWidth: true; wrapMode: Text.Wrap
                }
                Repeater {
                    model: backend.reportCard.dimensions || []
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        Text { text: modelData.name; color: "#303941"; Layout.preferredWidth: 72 }
                        Text { text: modelData.score; color: "#303941"; Layout.preferredWidth: 36; horizontalAlignment: Text.AlignRight }
                        Text { text: modelData.hint; color: "#99a2ab"; Layout.fillWidth: true; elide: Text.ElideRight }
                    }
                }
                Rectangle { visible: (backend.reportCard.notes || []).length > 0; Layout.fillWidth: true; height: 1; color: "#eff1f3" }
                Repeater {
                    model: backend.reportCard.notes || []
                    delegate: Text {
                        required property var modelData
                        text: "· " + modelData.text
                        color: "#303941"; wrapMode: Text.Wrap; Layout.fillWidth: true
                    }
                }
            }
        }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            QuietButton { objectName: "closeReport"; text: "关闭"; onClicked: backend.closeReport() }
        }
    }
}
