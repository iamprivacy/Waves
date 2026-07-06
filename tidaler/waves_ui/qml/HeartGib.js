.pragma library
// Footer easter-egg heart, a colour anatomical pixel heart that gibs when clicked.
// Stateless helper:
//   import "HeartGib.js" as HeartGib
// Every cell is { c, r, color } (a hex) so the gibs inherit each pixel's real colour.

// "Realistic" anatomical colorway (region → hex).
var CW = {
    o: "#2a0e14",   // outline / rim
    L: "#3f7fe0",   // left great vessels (blue, vena cava / pulmonary veins)
    A: "#d8342f",   // right great vessels (red, aorta / pulmonary trunk)
    a: "#e07a8e",   // atria (pink)
    f: "#e6c474",   // coronary fat (cream)
    v: "#c81f1f",   // ventricle (red)
    s: "#7c1414",   // shadow
    g: "#ffe1e1",   // gloss highlight
    c: "#ff5a4a"    // coronary artery
}

var COLS = 15, ROWS = 17
var FILL = [
    [[5, 6], [8, 9]], [[4, 6], [8, 10]], [[4, 6], [8, 11]],
    [[3, 12]], [[2, 13]], [[1, 13]], [[1, 14]], [[0, 14]],
    [[0, 14]], [[0, 13]], [[1, 13]], [[1, 12]], [[2, 11]],
    [[2, 10]], [[3, 9]], [[3, 8]], [[4, 7]]
]
var VESSELS = [
    { c: 5, r: 0 }, { c: 9, r: 0 }, { c: 9, r: 1 },
    { c: 7, r: 5 }, { c: 7, r: 6 }, { c: 7, r: 7 },
    { c: 8, r: 4 }, { c: 8, r: 5 }, { c: 8, r: 6 }, { c: 9, r: 7 }, { c: 9, r: 8 }, { c: 10, r: 9 }, { c: 10, r: 10 },
    { c: 6, r: 6 }, { c: 6, r: 7 }, { c: 5, r: 8 }, { c: 5, r: 9 }, { c: 4, r: 10 }, { c: 4, r: 11 }
]
function _inRanges(c, ranges) {
    for (var i = 0; i < ranges.length; i++) if (c >= ranges[i][0] && c <= ranges[i][1]) return true
    return false
}

// Build the heart model: { cols, rows, cells:[{c,r,color}] }.
function build() {
    var mask = []
    for (var r = 0; r < ROWS; r++) { mask.push([]); for (var c = 0; c < COLS; c++) mask[r].push(_inRanges(c, FILL[r])) }
    function lit(rr, cc) { return rr >= 0 && rr < ROWS && cc >= 0 && cc < COLS && mask[rr][cc] }
    function region(rr, cc) {
        if (rr <= 2) return (cc <= 6) ? "L" : "A"
        var edge = !lit(rr - 1, cc) || !lit(rr + 1, cc) || !lit(rr, cc - 1) || !lit(rr, cc + 1)
        if (edge) return "o"
        if (rr >= 3 && rr <= 5 && cc >= 2 && cc <= 4) return "g"
        if (rr >= 6 && rr <= 8 && cc >= 3 && cc <= 11) return "f"
        if (rr >= 3 && rr <= 6 && (cc <= 3 || cc >= 11)) return "a"
        if (rr >= 9 && cc >= 9) return "s"
        return "v"
    }
    var cells = []
    for (r = 0; r < ROWS; r++) for (c = 0; c < COLS; c++) if (mask[r][c]) cells.push({ c: c, r: r, color: CW[region(r, c)] })
    for (var i = 0; i < VESSELS.length; i++) cells.push({ c: VESSELS[i].c, r: VESSELS[i].r, color: CW.c })
    return { cols: COLS, rows: ROWS, cells: cells }
}

function draw(ctx, model, ox, oy, px) {
    for (var i = 0; i < model.cells.length; i++) {
        var cell = model.cells[i]
        ctx.fillStyle = cell.color
        ctx.fillRect(ox + cell.c * px, oy + cell.r * px, px, px)
    }
}
function cells(model, ox, oy, px) {
    var out = []
    for (var i = 0; i < model.cells.length; i++) {
        var cell = model.cells[i]
        out.push({ x: ox + cell.c * px + px / 2, y: oy + cell.r * px + px / 2, color: cell.color })
    }
    return out
}
function pxW(model, px) { return model.cols * px }
function pxH(model, px) { return model.rows * px }
