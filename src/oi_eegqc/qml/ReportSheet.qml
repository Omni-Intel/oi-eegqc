import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

QuietPopup {
    id: sheet
    objectName: "reportSheet"
    required property var backend
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(840, Overlay.overlay.width - 48)
    height: Math.min(760, Overlay.overlay.height - 48)
    property int detailPage: 0
    onVisibleChanged: if (visible) detailPage = 0
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
                        Text { text: modelData.hint; textFormat: Text.PlainText; color: "#77818b"; Layout.fillWidth: true; wrapMode: Text.Wrap }
                    }
                }
                Rectangle { visible: (backend.reportCard.notes || []).length > 0; Layout.fillWidth: true; height: 1; color: "#eff1f3" }
                Repeater {
                    model: (backend.reportCard.sections || []).length ? [] : (backend.reportCard.notes || [])
                    delegate: Text {
                        required property var modelData
                        text: "· " + modelData.text
                        color: "#303941"; wrapMode: Text.Wrap; Layout.fillWidth: true
                    }
                }
                Repeater {
                    model: backend.reportCard.sections || []
                    delegate: ColumnLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: 8
                        Text { text: modelData.title; textFormat: Text.PlainText; font.bold: true; color: "#303941"; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        Repeater {
                            model: modelData.lines
                            delegate: Text {
                                required property var modelData
                                text: modelData; textFormat: Text.PlainText
                                color: "#303941"; wrapMode: Text.Wrap; Layout.fillWidth: true
                            }
                        }
                    }
                }
                Text {
                    text: "异常窗口明细（相对文件起点，单位：秒）"
                    visible: (backend.reportCard.timeline || []).length > 0
                    font.bold: true; color: "#303941"; wrapMode: Text.Wrap; Layout.fillWidth: true
                }
                Repeater {
                    model: (backend.reportCard.timeline || []).slice(sheet.detailPage * 20, (sheet.detailPage + 1) * 20)
                    delegate: ColumnLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        Text { text: modelData.title; textFormat: Text.PlainText; font.bold: true; color: "#303941"; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        Text { text: modelData.text; textFormat: Text.PlainText; color: "#535e68"; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    }
                }
                RowLayout {
                    visible: (backend.reportCard.timeline || []).length > 20
                    QuietButton { text: "上一页"; enabled: sheet.detailPage > 0; onClicked: sheet.detailPage-- }
                    Text { text: (sheet.detailPage + 1) + " / " + Math.ceil((backend.reportCard.timeline || []).length / 20); color: "#303941" }
                    QuietButton { text: "下一页"; enabled: (sheet.detailPage + 1) * 20 < (backend.reportCard.timeline || []).length; onClicked: sheet.detailPage++ }
                }
            }
        }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            QuietButton { objectName: "closeReport"; text: "关闭"; onClicked: backend.closeReport() }
        }
    }
}
