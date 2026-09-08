import QtQuick
import QtQuick.Controls.Basic

ComboBox {
    id: control
    implicitHeight: 38
    leftPadding: 12; rightPadding: 30
    hoverEnabled: true
    background: Rectangle { radius: 6; color: control.hovered ? "#f8f9fa" : "white"; border.color: control.activeFocus ? "#758a9e" : "#e0e4e8" }
    contentItem: Text { text: control.displayText; font: control.font; color: "#303941"; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
    indicator: Text { text: "⌄"; color: "#707b85"; x: control.width-24; anchors.verticalCenter: parent.verticalCenter }
    delegate: ItemDelegate {
        width: control.width; text: modelData; font: control.font; highlighted: control.highlightedIndex === index
        contentItem: Text { text: modelData; color: "#303941"; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { color: parent.highlighted ? "#edf1f4" : "white"; radius: 4 }
    }
    popup: Popup {
        y: control.height + 5; width: control.width; padding: 5
        implicitHeight: contentItem.implicitHeight + 10
        contentItem: ListView { clip: true; implicitHeight: contentHeight; model: control.popup.visible ? control.delegateModel : null; currentIndex: control.highlightedIndex }
        background: Rectangle { color: "white"; radius: 7; border.color: "#e0e4e8" }
    }
}
