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
                Text { text: sheet.info.folderCount + " 个文件夹 · " + sheet.info.count + " 个文件 · " + sheet.info.size; color: "#303941"; Layout.fillWidth: true; wrapMode: Text.Wrap }
                Text { text: "包含子文件夹内全部文件"; color: "#89929b"; font.pixelSize: 11 }
                Repeater {
                    objectName: "uploadFolderList"
                    model: sheet.info.folders
                    delegate: Rectangle {
                        required property var modelData
                        objectName: "uploadFolderCard"
                        Layout.fillWidth: true
                        implicitHeight: folderInfo.implicitHeight + 24
                        radius: 8; color: "#f7f8fa"
                        ColumnLayout {
                            id: folderInfo
                            anchors.fill: parent; anchors.margins: 12; spacing: 6
                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: modelData.name.toLowerCase(); textFormat: Text.PlainText; color: "#303941"; font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideMiddle }
                                Text { text: modelData.count + " 个文件 · " + modelData.size; color: "#7c8791"; font.pixelSize: 11 }
                            }
                            Text { text: modelData.path.toLowerCase(); textFormat: Text.PlainText; color: "#89929b"; font.pixelSize: 11; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true }
                            Text { text: "采集 " + modelData.uploadId; visible: modelData.uploadId !== ""; textFormat: Text.PlainText; color: "#89929b"; font.pixelSize: 11; elide: Text.ElideMiddle; Layout.fillWidth: true }
                        }
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
        Flow {
            Layout.fillWidth: true; spacing: 8
            visible: !sheet.backend.upload.active && (!sheet.info.failed || sheet.info.uncertain)
            QuietButton { objectName: "newAcquisition"; text: "作为新采集上传"; visible: sheet.info.started || sheet.info.uncertain; enabled: sheet.backend.canUpload; onClicked: newConfirm.open() }
            QuietButton { objectName: "restoreUploadId"; text: "恢复编号"; visible: sheet.info.uncertain; onClicked: restoreDialog.open() }
            QuietButton { objectName: "resetUploadRecord"; text: "重置上传记录"; visible: sheet.info.folderCount > 0; onClicked: resetFirst.open() }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            QuietButton { text: sheet.backend.upload.active ? "收起" : "关闭"; onClicked: sheet.backend.upload.close() }
            Item { Layout.fillWidth: true }
            QuietButton { objectName: "cancelUpload"; text: "取消上传"; visible: sheet.backend.upload.active; enabled: sheet.backend.upload.canCancel; onClicked: sheet.backend.upload.cancel() }
            QuietButton {
                objectName: "startUpload"
                text: sheet.info.failed ? "重试" : sheet.info.started ? "继续上传" : "上传"
                visible: !sheet.backend.upload.active && !sheet.info.completed && !sheet.info.uncertain
                enabled: sheet.backend.upload.canStart && sheet.backend.canUpload
                primary: true
                onClicked: sheet.backend.upload.start()
            }
        }
    }
    QuietPopup {
        id: resetFirst
        objectName: "resetUploadFirst"
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(440, sheet.width); modal: true
        contentItem: ColumnLayout {
            spacing: 16
            Text { text: "重置上传记录"; color: "#303941"; font.pixelSize: 16 }
            ComboBox { id: resetFolder; objectName: "resetFolderChoice"; Layout.fillWidth: true; model: sheet.info.folders; textRole: "path" }
            Text { text: "只清除当前选中文件夹的本地采集编号、续传状态和历史映射。不删除本地文件或云端数据。重置后下次上传会申请新编号。"; color: "#7c8791"; Layout.fillWidth: true; wrapMode: Text.Wrap }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                QuietButton { text: "取消"; onClicked: resetFirst.close() }
                QuietButton { objectName: "resetFirstConfirm"; text: "确认重置"; onClicked: { resetSecond.targetRoot = sheet.info.folders[resetFolder.currentIndex].path; resetFirst.close(); resetSecond.open(); } }
            }
        }
    }
    QuietPopup {
        id: resetSecond
        objectName: "resetUploadSecond"
        property string targetRoot: ""
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(440, sheet.width); modal: true
        contentItem: ColumnLayout {
            spacing: 16
            Text { text: "再次确认"; color: "#303941"; font.pixelSize: 16 }
            Text { text: resetSecond.targetRoot; textFormat: Text.PlainText; color: "#7c8791"; Layout.fillWidth: true; wrapMode: Text.WrapAnywhere }
            Text { text: "将清除该文件夹的本地编号、续传状态和历史映射，不能撤销。不会删除本地文件或云端数据；下次上传申请新编号，可能重复保存云端数据。"; color: "#a04f3e"; Layout.fillWidth: true; wrapMode: Text.Wrap }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                QuietButton { text: "取消"; onClicked: resetSecond.close() }
                QuietButton { objectName: "resetSecondConfirm"; text: "确认清除记录"; onClicked: { resetSecond.close(); sheet.backend.upload.resetUploadRecord(resetSecond.targetRoot); } }
            }
        }
    }
    QuietPopup {
        id: newConfirm
        objectName: "newAcquisitionConfirm"
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(420, sheet.width); modal: true
        contentItem: ColumnLayout {
            spacing: 16
            Text { text: "作为新采集上传"; color: "#303941"; font.pixelSize: 16 }
            Text { text: "将为所选文件夹创建新的采集编号，原云端数据保留。"; color: "#7c8791"; Layout.fillWidth: true; wrapMode: Text.Wrap }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                QuietButton { text: "取消"; onClicked: newConfirm.close() }
                QuietButton { objectName: "confirmNewAcquisition"; text: "确认新采集"; primary: true; onClicked: { newConfirm.close(); sheet.backend.upload.newAcquisition(); } }
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
