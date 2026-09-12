import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Popup {
    id: sheet
    objectName: "uploadSheet"
    required property var backend
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(560, Overlay.overlay.width - 48)
    height: Math.min(500, Overlay.overlay.height - 40)
    padding: 20
    modal: true
    focus: true
    visible: backend.upload.opened
    onAboutToHide: if (backend.upload.opened) backend.upload.close()
    background: Rectangle { color: "white"; radius: 10; border.color: "#dce2e7" }
    contentItem: ColumnLayout {
        spacing: 14
        Text { text: "上传文件夹"; color: "#303941"; font.pixelSize: 16 }
        ScrollView {
            Layout.fillWidth: true; Layout.fillHeight: true
            contentWidth: availableWidth; clip: true
            ColumnLayout {
                width: parent.width
                spacing: 12
                Text { text: sheet.backend.upload.info.roots; textFormat: Text.PlainText; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true; color: "#303941" }
                Text { text: sheet.backend.upload.info.count + " 个文件 · " + sheet.backend.upload.info.size; color: "#7c8791" }
                Text { text: sheet.backend.upload.info.uploadId; textFormat: Text.PlainText; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true; color: "#7c8791" }
                Text { text: sheet.backend.upload.info.status; color: "#303941" }
                ProgressBar { Layout.fillWidth: true; value: sheet.backend.upload.info.progress; indeterminate: sheet.backend.upload.active && sheet.backend.upload.info.uploadId === "" }
                Text { text: Math.floor(sheet.backend.upload.info.progress * 100) + "% · " + sheet.backend.upload.info.speed; color: "#7c8791" }
                Text { text: sheet.backend.upload.info.current; textFormat: Text.PlainText; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true; color: "#7c8791" }
                Text { text: sheet.backend.upload.info.error; textFormat: Text.PlainText; visible: text !== ""; wrapMode: Text.Wrap; Layout.fillWidth: true; color: "#a04f3e" }
            }
        }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            QuietButton { text: "关闭"; onClicked: sheet.backend.upload.close() }
            QuietButton { text: "恢复编号"; visible: sheet.backend.upload.info.uncertain; enabled: !sheet.backend.upload.active; onClicked: restoreDialog.open() }
            QuietButton { objectName: "cancelUpload"; text: "取消上传"; visible: sheet.backend.upload.active; enabled: sheet.backend.upload.canCancel; onClicked: sheet.backend.upload.cancel() }
            QuietButton { objectName: "startUpload"; text: "开始 / 继续"; visible: !sheet.backend.upload.active; enabled: sheet.backend.upload.canStart && sheet.backend.canUpload; primary: true; onClicked: sheet.backend.upload.start() }
        }
    }
    Dialog {
        id: restoreDialog
        parent: Overlay.overlay; anchors.centerIn: parent; width: Math.min(400, sheet.width); modal: true
        title: "恢复原批次编号"
        contentItem: ColumnLayout {
            TextField { id: restoreInput; Layout.fillWidth: true; placeholderText: "仅填写管理员核实的原编号" }
            QuietButton { text: "确认"; onClicked: { if (sheet.backend.upload.restoreUploadId(restoreInput.text)) { restoreInput.clear(); restoreDialog.close(); } } }
        }
    }
}
