import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

QuietPopup {
    id: sheet
    objectName: "uploadSheet"
    required property var backend
    readonly property var info: backend.upload.info
    readonly property bool compact: Overlay.overlay.height < 440
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(520, Overlay.overlay.width - 40)
    padding: compact ? 20 : 24
    height: Math.min(content.implicitHeight + topPadding + bottomPadding, Overlay.overlay.height - 40)
    modal: true
    visible: backend.upload.opened
    onAboutToHide: if (backend.upload.opened) backend.upload.close()
    contentItem: ColumnLayout {
        id: content
        spacing: sheet.compact ? 12 : 20
        RowLayout {
            Layout.fillWidth: true
            Text { text: "上传文件夹"; color: "#303941"; font.pixelSize: 17; font.weight: Font.DemiBold; Layout.fillWidth: true }
            Text { text: sheet.info.status; color: sheet.info.failed ? "#a04f3e" : "#7c8791"; font.pixelSize: 12 }
        }
        ScrollView {
            id: body
            Layout.fillWidth: true
            Layout.fillHeight: true
            implicitHeight: details.implicitHeight
            contentWidth: availableWidth
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                id: details
                width: body.availableWidth
                spacing: sheet.compact ? 12 : 18
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: folderInfo.implicitHeight + 28
                    radius: 8; color: "#f7f8fa"
                    ColumnLayout {
                        id: folderInfo
                        anchors.fill: parent; anchors.margins: 14; spacing: 8
                        Text {
                            text: sheet.info.roots || "正在读取文件夹…"
                            textFormat: Text.PlainText; color: "#303941"
                            wrapMode: Text.WrapAnywhere; Layout.fillWidth: true
                            maximumLineCount: 3; elide: Text.ElideMiddle
                        }
                        Text { text: sheet.info.count + " 个文件 · " + sheet.info.size; color: "#7c8791"; font.pixelSize: 12 }
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 9
                    visible: sheet.backend.upload.active || sheet.info.started || sheet.info.completed
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: sheet.info.current || (sheet.info.completed ? "全部上传完成" : sheet.info.preparing ? "正在准备…" : "等待上传")
                            textFormat: Text.PlainText; elide: Text.ElideMiddle
                            color: "#7c8791"; font.pixelSize: 12; Layout.fillWidth: true
                        }
                        Text { text: Math.floor(sheet.info.progress * 100) + "%"; visible: !sheet.info.preparing; color: "#303941"; font.pixelSize: 12 }
                    }
                    QuietProgress { objectName: "uploadProgress"; Layout.fillWidth: true; value: sheet.info.progress; indeterminate: sheet.info.preparing }
                    Text { text: sheet.info.speed; visible: sheet.backend.upload.active && !sheet.info.preparing; color: "#99a2ab"; font.pixelSize: 11 }
                }
                Text {
                    text: "批次 " + sheet.info.uploadId; visible: sheet.info.uploadId !== ""
                    textFormat: Text.PlainText; elide: Text.ElideMiddle
                    Layout.fillWidth: true; color: "#99a2ab"; font.pixelSize: 11
                }
            }
        }
        Text {
            objectName: "uploadError"
            text: sheet.info.error; visible: text !== ""
            textFormat: Text.PlainText; wrapMode: Text.Wrap
            Layout.fillWidth: true; color: "#a04f3e"; font.pixelSize: 12
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            QuietButton { text: sheet.backend.upload.active ? "收起" : "关闭"; onClicked: sheet.backend.upload.close() }
            Item { Layout.fillWidth: true }
            QuietButton { text: "恢复编号"; visible: sheet.info.uncertain; enabled: !sheet.backend.upload.active; onClicked: restoreDialog.open() }
            QuietButton { objectName: "cancelUpload"; text: "取消上传"; visible: sheet.backend.upload.active; enabled: sheet.backend.upload.canCancel; onClicked: sheet.backend.upload.cancel() }
            QuietButton {
                objectName: "startUpload"
                text: sheet.info.failed ? "重试" : sheet.info.started ? "继续上传" : "上传"
                visible: !sheet.backend.upload.active && !sheet.info.completed
                enabled: sheet.backend.upload.canStart && sheet.backend.canUpload
                primary: true
                onClicked: sheet.backend.upload.start()
            }
        }
    }
    QuietPopup {
        id: restoreDialog
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(400, sheet.width); modal: true
        contentItem: ColumnLayout {
            spacing: 16
            Text { text: "恢复原批次编号"; color: "#303941"; font.pixelSize: 16 }
            TextField {
                id: restoreInput
                Layout.fillWidth: true; placeholderText: "仅填写管理员核实的原编号"
                padding: 12; selectByMouse: true; color: "#303941"
                background: Rectangle { radius: 7; color: "#f7f8fa"; border.color: restoreInput.activeFocus ? "#758a9e" : "#e0e5ea" }
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                QuietButton { text: "取消"; onClicked: restoreDialog.close() }
                QuietButton { text: "确认"; primary: true; onClicked: { if (sheet.backend.upload.restoreUploadId(restoreInput.text)) { restoreInput.clear(); restoreDialog.close(); } } }
            }
        }
    }
}
