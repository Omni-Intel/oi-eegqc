import QtQuick
import QtQuick.Controls.Basic

ProgressBar {
    id: control
    implicitHeight: 6
    padding: 0
    background: Rectangle { implicitHeight: 6; radius: 3; color: "#edf0f3" }
    contentItem: Item {
        clip: true
        Rectangle {
            visible: !control.indeterminate
            width: parent.width * control.position
            height: parent.height; radius: 3; color: "#748a9d"
            Behavior on width { NumberAnimation { duration: 140 } }
        }
        Rectangle {
            visible: control.indeterminate
            width: parent.width * 0.26; height: parent.height; radius: 3; color: "#748a9d"
            XAnimator on x {
                from: -control.width * 0.26; to: control.width
                duration: 1250; loops: Animation.Infinite
                running: control.indeterminate && control.visible
            }
        }
    }
}
