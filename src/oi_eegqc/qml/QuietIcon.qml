import QtQuick

Canvas {
    property string kind: "file"
    property color ink: "#535e68"
    width: 16; height: 16
    onInkChanged: requestPaint()
    onKindChanged: requestPaint()
    onPaint: {
        let c = getContext("2d"); c.reset(); c.strokeStyle = ink; c.lineWidth = 1.25; c.lineJoin = "round"; c.lineCap = "round";
        c.beginPath();
        if (kind === "file") {
            c.moveTo(4,1.5); c.lineTo(9,1.5); c.lineTo(13,5.5); c.lineTo(13,14.5); c.lineTo(3,14.5); c.lineTo(3,1.5); c.lineTo(4,1.5);
            c.moveTo(9,1.5); c.lineTo(9,5.5); c.lineTo(13,5.5);
        } else if (kind === "folder") {
            c.moveTo(1.5,4); c.lineTo(1.5,13); c.lineTo(14.5,13); c.lineTo(14.5,5); c.lineTo(8,5); c.lineTo(6,3); c.lineTo(1.5,3); c.lineTo(1.5,4);
        } else if (kind === "settings") {
            for (let i=0;i<3;i++) { let y=3+i*5; let x=i===1?10:5; c.moveTo(2,y); c.lineTo(x-2,y); c.moveTo(x+2,y); c.lineTo(14,y); c.moveTo(x+2,y); c.arc(x,y,2,0,Math.PI*2); }
        } else if (kind === "close") {
            c.moveTo(4,4); c.lineTo(12,12); c.moveTo(12,4); c.lineTo(4,12);
        }
        c.stroke();
    }
}
