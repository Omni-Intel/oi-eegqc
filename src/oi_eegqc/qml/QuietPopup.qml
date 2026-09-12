import QtQuick
import QtQuick.Controls.Basic

Popup {
    padding: 24
    focus: true
    background: Rectangle { color: "#ffffff"; radius: 12; border.color: "#e0e5ea" }
    Overlay.modal: Rectangle { color: "#330f172a" }
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 120 } }
    exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: 90 } }
}
