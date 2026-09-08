import QtQuick
import QtQuick.Controls.Basic

Button {
    id: control
    property bool primary: false
    property string symbol: ""
    implicitHeight: 38
    implicitWidth: Math.max(38, caption.implicitWidth + (symbol ? 28 : 0) + 28)
    padding: 14
    hoverEnabled: true
    activeFocusOnTab: true
    Accessible.name: text
    background: Rectangle {
        radius: 7
        color: !control.enabled ? "#f1f2f3" : control.primary ? (control.down ? "#14191e" : control.hovered ? "#3b4249" : "#272e35") : (control.down ? "#e8ebee" : control.hovered ? "#f1f3f5" : "#ffffff")
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? "#758a9e" : control.primary ? "transparent" : "#e0e4e8"
        Behavior on color { ColorAnimation { duration: 100 } }
    }
    contentItem: Item {
        implicitHeight: caption.implicitHeight
        Row {
            anchors.centerIn: parent; spacing: 8
            opacity: control.enabled ? 1 : 0.38
            QuietIcon { visible: control.symbol !== ""; kind: control.symbol; ink: control.primary ? "white" : "#535e68"; anchors.verticalCenter: parent.verticalCenter }
            Text { id: caption; text: control.text; font: control.font; color: control.primary && control.enabled ? "#ffffff" : "#303941"; anchors.verticalCenter: parent.verticalCenter; textFormat: Text.PlainText }
        }
    }
}
