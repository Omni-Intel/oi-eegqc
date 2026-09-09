import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: window
    required property var backend
    property bool settingsOpen: false
    width: 760; height: 520
    minimumWidth: 600; minimumHeight: 380
    visible: true
    title: "Omni-Intelligence EEG Quality Control App"
    color: "#f7f8fa"
    font.family: Qt.platform.os === "windows" ? "Microsoft YaHei" : "sans-serif"
    font.pixelSize: 13
    onClosing: function(close) { close.accepted = backend.requestClose() }
    Connections { target: backend; function onCloseReady() { window.close() } }
    Shortcut { sequences: [StandardKey.Open]; enabled: !backend.busy && !metadata.opened; onActivated: files.open() }
    Shortcut { sequences: [StandardKey.SelectAll]; enabled: !backend.busy && !settings.opened; onActivated: backend.selectAll() }
    Shortcut { sequence: "Return"; enabled: !backend.busy && !metadata.opened && !backend.channelsOpen; onActivated: backend.openSelectedReport() }
    Shortcut { sequence: "Enter"; enabled: !backend.busy && !metadata.opened && !backend.channelsOpen; onActivated: backend.openSelectedReport() }

    FileDialog {
        id: files
        title: "选择文件"
        fileMode: FileDialog.OpenFiles
        nameFilters: ["脑电文件 (*.edf *.edf+ *.bdf *.npy)"]
        onAccepted: backend.addUrls(selectedFiles)
    }
    FolderDialog { id: folders; title: "选择文件夹"; onAccepted: backend.addUrls([selectedFolder]) }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 24; spacing: 18
        RowLayout {
            spacing: 9
            QuietButton { objectName: "chooseFiles"; text: "选择文件"; symbol: "file"; enabled: !backend.busy; onClicked: files.open() }
            QuietButton { objectName: "chooseFolder"; text: "选择文件夹"; symbol: "folder"; enabled: !backend.busy; onClicked: folders.open() }
            Item { Layout.fillWidth: true }
            QuietButton { objectName: "settingsButton"; text: "设置"; symbol: "settings"; enabled: !backend.busy; onClicked: window.settingsOpen = !window.settingsOpen }
        }
        Rectangle {
            id: sheet
            Layout.fillWidth: true; Layout.fillHeight: true
            radius: 10; color: "#ffffff"; border.color: drop.containsDrag ? "#8294a5" : "#e5e8eb"
            Behavior on border.color { ColorAnimation { duration: 100 } }
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 1; spacing: 0
                RowLayout {
                    visible: backend.count > 0
                    Layout.fillWidth: true; Layout.leftMargin: 18; Layout.rightMargin: 18; Layout.preferredHeight: 44
                    Text { text: "文件"; color: "#89929b"; Layout.fillWidth: true }
                    Text { text: "分数"; color: "#89929b"; Layout.preferredWidth: 76; horizontalAlignment: Text.AlignRight }
                    Text { text: ""; Layout.preferredWidth: 44 }
                    Text { text: "状态"; color: "#89929b"; Layout.preferredWidth: 96; horizontalAlignment: Text.AlignRight }
                }
                Rectangle { visible: backend.count > 0; Layout.fillWidth: true; height: 1; color: "#eff1f3" }
                ListView {
                    id: list
                    objectName: "fileList"
                    Layout.fillWidth: true; Layout.fillHeight: true
                    clip: true; model: backend.model; boundsBehavior: Flickable.StopAtBounds
                    reuseItems: true; cacheBuffer: 300; flickDeceleration: 1800
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded; width: 7 }
                    focus: true
                    Keys.onPressed: function(event) {
                        if (backend.busy) return;
                        if (event.key === Qt.Key_Up || event.key === Qt.Key_Down) {
                            currentIndex = Math.max(0, Math.min(count-1, currentIndex + (event.key === Qt.Key_Down ? 1 : -1)));
                            backend.select(currentIndex, event.modifiers); positionViewAtIndex(currentIndex, ListView.Contain); event.accepted = true;
                        }
                    }
                    delegate: Rectangle {
                        id: row
                        required property int index
                        required property string label
                        required property string score
                        required property string state
                        required property string detail
                        required property bool chosen
                        required property bool ready
                        width: ListView.view.width; height: 54
                        color: chosen ? "#eaf0f5" : mouse.containsMouse ? "#f6f8fa" : "transparent"
                        Behavior on color { ColorAnimation { duration: 90 } }
                        Rectangle { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; width: 3; height: 20; radius: 1; color: "#6c8296"; visible: row.chosen }
                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 18; anchors.rightMargin: 18; spacing: 12
                            Text { text: row.label; textFormat: Text.PlainText; color: "#303941"; elide: Text.ElideMiddle; Layout.fillWidth: true }
                            Text { text: row.score; color: "#303941"; font.pixelSize: 15; Layout.preferredWidth: 76; horizontalAlignment: Text.AlignRight }
                            Text {
                                text: row.ready ? "查看" : ""
                                color: "#6c8296"
                                Layout.preferredWidth: 44
                                horizontalAlignment: Text.AlignRight
                                MouseArea {
                                    anchors.fill: parent
                                    enabled: row.ready
                                    onClicked: function(event) {
                                        list.forceActiveFocus(); list.currentIndex = row.index
                                        backend.select(row.index, 0)
                                        backend.openReport(row.index)
                                        event.accepted = true
                                    }
                                }
                            }
                            Text { text: row.state; color: "#88939d"; Layout.preferredWidth: 96; horizontalAlignment: Text.AlignRight }
                        }
                        Rectangle { anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right; anchors.leftMargin: 18; anchors.rightMargin: 18; height: 1; color: "#f0f2f4" }
                        MouseArea {
                            id: mouse; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.LeftButton | Qt.RightButton
                            onClicked: function(event) {
                                list.forceActiveFocus(); list.currentIndex = row.index;
                                if (event.button === Qt.RightButton) {
                                    if (!row.chosen) backend.select(row.index, 0);
                                    if (!backend.busy) rowMenu.popup();
                                } else backend.select(row.index, event.modifiers);
                            }
                            onDoubleClicked: function(event) {
                                if (row.ready) backend.openReport(row.index);
                            }
                        }
                        ToolTip.visible: mouse.containsMouse && !backend.busy
                        ToolTip.delay: 700
                        ToolTip.text: row.detail
                    }
                    Text {
                        anchors.centerIn: parent
                        visible: backend.count === 0
                        text: drop.containsDrag ? "松开以导入" : "拖入文件或文件夹"
                        color: "#99a2ab"; font.pixelSize: 14
                    }
                }
            }
            DropArea {
                id: drop; anchors.fill: parent; enabled: !backend.busy
                onEntered: function(drag) { drag.accepted = drag.hasUrls }
                onDropped: function(event) { if (event.hasUrls) { backend.addUrls(event.urls); event.acceptProposedAction(); } }
            }
            Rectangle {
                anchors.fill: parent; radius: 10; color: "#0b6c8296"; visible: drop.containsDrag
                border.color: "#8294a5"
            }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            Text { text: backend.summary; color: "#7c8791"; Layout.fillWidth: true; elide: Text.ElideRight }
            QuietButton { text: "移除 " + backend.selectedCount; visible: backend.selectedCount > 0 && !backend.busy; onClicked: backend.remove(false) }
            QuietButton { objectName: "scoreButton"; text: backend.busy ? "取消" : "评分"; primary: !backend.busy; implicitWidth: 94; enabled: backend.busy ? !backend.stopping : backend.canScore; onClicked: backend.scoreOrStop() }
        }
    }
    Rectangle {
        anchors.bottom: parent.bottom; width: parent.width; height: 2; color: "#e5e9ed"; visible: backend.busy
        Rectangle {
            width: parent.width * 0.23; height: 2; color: "#8395a5"
            XAnimator on x { from: -window.width * 0.23; to: window.width; duration: 1350; loops: Animation.Infinite; running: backend.busy }
        }
    }
    Menu {
        id: rowMenu
        MenuItem { text: "移除"; onTriggered: backend.remove(false) }
        MenuItem { text: "清空"; onTriggered: backend.remove(true) }
    }

    Popup {
        id: settings
        objectName: "settingsPanel"
        x: window.width - width - 24; y: 68; width: 300; padding: 20
        visible: window.settingsOpen
        modal: false; focus: true
        onClosed: window.settingsOpen = false
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: "#ffffff"; radius: 10; border.color: "#dce2e7" }
        enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 110 } }
        exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: 80 } }
        contentItem: ColumnLayout {
            spacing: 12
            Text { text: "数组排列"; color: "#7c8791" }
            QuietCombo { objectName: "orderCombo"; Layout.fillWidth: true; model: ["默认", "通道 × 采样点", "采样点 × 通道"]; currentIndex: backend.orderIndex; onActivated: backend.saveSettings(currentIndex, backend.mains) }
            Text { text: "电网频率"; color: "#7c8791"; Layout.topMargin: 4 }
            QuietCombo { Layout.fillWidth: true; model: ["50 赫兹", "60 赫兹"]; currentIndex: backend.mains === 60 ? 1 : 0; onActivated: backend.saveSettings(backend.orderIndex, currentIndex === 1 ? 60 : 50) }
            CheckBox {
                id: allChannels
                objectName: "allChannels"
                Layout.fillWidth: true
                Layout.topMargin: 4
                text: "全通道"
                checked: backend.allChannels
                palette.windowText: "#303941"
                onClicked: backend.setAllChannels(checked)
            }
            QuietButton {
                objectName: "pickChannels"
                text: "选择通道"
                visible: !backend.allChannels
                Layout.fillWidth: true
                onClicked: backend.openChannelSheet()
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: "#eff1f3"; Layout.topMargin: 6; Layout.bottomMargin: 4 }
            Text { text: "当前版本 " + backend.version; color: "#99a2ab" }
            QuietButton { objectName: "checkUpdateButton"; text: backend.checking ? "正在检查…" : "检查更新"; enabled: !backend.checking; Layout.fillWidth: true; onClicked: backend.checkUpdate() }
            Text { visible: backend.updateText !== "" && !backend.checking; text: backend.updateText; color: "#7c8791"; Layout.fillWidth: true; wrapMode: Text.Wrap }
            QuietButton { text: "打开下载页"; visible: backend.updateAvailable; Layout.fillWidth: true; onClicked: backend.openUpdate() }
        }
    }
    Dialog {
        id: metadata
        anchors.centerIn: parent; width: 340; padding: 24; modal: true; focus: true
        closePolicy: Popup.NoAutoClose
        background: Rectangle { color: "white"; radius: 10; border.color: "#dce2e7" }
        contentItem: ColumnLayout {
            spacing: 12
            Text { text: backend.parameters.name || ""; textFormat: Text.PlainText; elide: Text.ElideMiddle; Layout.fillWidth: true; color: "#303941" }
            Text { text: "采样率"; color: "#7c8791"; Layout.topMargin: 8 }
            TextField { id: rate; objectName: "samplingRate"; Layout.fillWidth: true; implicitHeight: 38; placeholderText: "请输入实际采样率"; inputMethodHints: Qt.ImhFormattedNumbersOnly; selectByMouse: true; color: "#303941"; background: Rectangle { radius: 6; border.color: rate.activeFocus ? "#758a9e" : "#e0e4e8" } }
            Text { text: "单位"; color: "#7c8791" }
            QuietCombo { id: unit; Layout.fillWidth: true; model: ["微伏", "毫伏", "伏"] }
            CheckBox { id: reuse; text: "应用于其余数组文件"; palette.windowText: "#7c8791" }
            RowLayout {
                Layout.alignment: Qt.AlignRight; Layout.topMargin: 6
                QuietButton { text: "跳过"; onClicked: backend.skipParameters() }
                QuietButton { text: "确定"; primary: true; enabled: Number(rate.text) > 0 && Number(rate.text) <= 1000000; onClicked: backend.acceptParameters(rate.text, unit.currentIndex, reuse.checked) }
            }
        }
    }
    ChannelSheet { backend: backend }
    ReportSheet { backend: backend }
    Connections {
        target: backend
        function onParametersChanged() {
            if (backend.parameters.name) {
                rate.text = String(backend.parameters.sfreq); unit.currentIndex = backend.parameters.unit; reuse.checked = false;
                metadata.open(); rate.forceActiveFocus();
            } else metadata.close();
        }
        function onChanged() {
            if (allChannels.checked !== backend.allChannels)
                allChannels.checked = backend.allChannels
        }
    }
}
