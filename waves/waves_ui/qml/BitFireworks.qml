import QtQuick

// Bit-styled crackle fireworks: square pixels on a fixed grid. A rocket
// with a short trail climbs to each burst point and opens into a green or
// white peony; as its sparks burn out, each pops into a little crackle in
// the shell's own color that strobes for a few frames, so every shell ends
// in a fizz of tiny flashes. Stateless apart from the sparks in flight:
//   fire()     plays the whole show once (the schedule below)
//   release()  fades whatever is still up (the caller is moving on)
// Burst points are offsets from the canvas center.
Canvas {
    id: fx
    property var colors: ["#3dff6e", "#e6e8ec", "#ffb01f", "#56c8d8"]
    property int cell: 3                 // one "bit", in px
    // { at: ms after fire(), x, y: burst point from center, c: colors index }
    property var shows: [
        { at: 0,    x: -150, y: 40,  c: 0 },
        { at: 280,  x: 170,  y: -70, c: 1 },
        { at: 580,  x: 20,   y: 60,  c: 0 },
        { at: 860,  x: -270, y: 70,  c: 1 },
        { at: 1060, x: 230,  y: 40,  c: 0 }
    ]
    property bool running: false
    property real clock: 0               // seconds since fire()
    property real fadeT: -1              // seconds since release() (-1: not yet)
    property int next: 0
    property var sparks: []
    renderTarget: Canvas.Image

    function fire() {
        sparks = []; next = 0; clock = 0; fadeT = -1; opacity = 1; running = true
    }
    function release() { if (running && fadeT < 0) fadeT = 0 }

    function _launch(s) {
        var cx = width / 2 + s.x, cy = height / 2 + s.y
        sparks.push({ kind: 0, x: cx + (Math.random() - 0.5) * 24, y: height - 2,
                      tx: cx, ty: cy, t: 0, life: 0.34, c: s.c, trail: [] })
    }
    function _burst(r) {
        var n = 24 + Math.floor(Math.random() * 6)
        for (var i = 0; i < n; i++) {
            var a = Math.random() * Math.PI * 2
            // Filled, not a ring: speeds spread so the shell is a solid ball.
            var v = 60 + Math.random() * 170
            sparks.push({ kind: 1, x: r.tx, y: r.ty, vx: Math.cos(a) * v, vy: Math.sin(a) * v,
                          t: 0, life: 0.55 + Math.random() * 0.5, c: r.c, trail: [] })
        }
        sparks.push({ kind: 2, x: r.tx, y: r.ty, t: 0, life: 0.14, c: 1, trail: [] })
    }

    FrameAnimation { running: fx.running
        onTriggered: { fx.step(Math.min(frameTime, 0.032)); fx.requestPaint() } }

    function step(dt) {
        clock += dt
        if (fadeT >= 0) { fadeT += dt; opacity = Math.max(0, 1 - fadeT / 0.25) }
        while (fadeT < 0 && next < shows.length && clock * 1000 >= shows[next].at) _launch(shows[next++])
        var live = []
        for (var i = 0; i < sparks.length; i++) {
            var p = sparks[i]
            p.t += dt
            p.trail.unshift({ x: p.x, y: p.y })
            if (p.trail.length > 3) p.trail.pop()
            if (p.kind === 0) {
                var k = Math.min(1, p.t / p.life), e = 1 - (1 - k) * (1 - k)
                p.x = p.x + (p.tx - p.x) * Math.min(1, dt * 18 * (0.4 + k))
                p.y = height - 2 + (p.ty - height + 2) * e
                if (k >= 1) { _burst(p); continue }
            } else if (p.kind === 1) {
                p.vx *= Math.pow(0.18, dt); p.vy *= Math.pow(0.18, dt)
                p.vy += 140 * dt
                p.x += p.vx * dt; p.y += p.vy * dt
                // Burnt out: most sparks pop into a crackle.
                if (p.t >= p.life && Math.random() < 0.75)
                    sparks.push({ kind: 3, x: p.x + (Math.random() - 0.5) * 8, y: p.y + (Math.random() - 0.5) * 8,
                                  t: -Math.random() * 0.12, life: 0.16, c: p.c, trail: [] })
            }
            if (p.kind !== 0 && p.t >= p.life) continue
            live.push(p)
        }
        sparks = live
        if ((fadeT < 0 && next >= shows.length && sparks.length === 0) || fadeT > 0.25) {
            running = false; sparks = []
        }
    }

    function _level(f) { return f < 0.45 ? 1 : f < 0.7 ? 0.7 : f < 0.88 ? 0.45 : 0.22 }
    function _dot(ctx, x, y, s) {
        var g = fx.cell
        ctx.fillRect(Math.round(x / g) * g, Math.round(y / g) * g, s, s)
    }
    onPaint: {
        var ctx = getContext("2d"); ctx.clearRect(0, 0, width, height)
        if (!running) return
        var g = cell
        for (var i = 0; i < sparks.length; i++) {
            var p = sparks[i]
            ctx.fillStyle = colors[p.c]
            if (p.kind === 3) {
                // A crackle: a tiny x of bits, in its shell's color, that strobes until it is spent.
                if (p.t < 0 || Math.random() < 0.4) continue
                ctx.globalAlpha = 1
                _dot(ctx, p.x, p.y, g)
                _dot(ctx, p.x - g, p.y - g, g); _dot(ctx, p.x + g, p.y + g, g)
                _dot(ctx, p.x - g, p.y + g, g); _dot(ctx, p.x + g, p.y - g, g)
                continue
            }
            if (p.kind === 2) {
                ctx.globalAlpha = 1 - p.t / p.life
                _dot(ctx, p.x, p.y, g); _dot(ctx, p.x - g, p.y, g); _dot(ctx, p.x + g, p.y, g)
                _dot(ctx, p.x, p.y - g, g); _dot(ctx, p.x, p.y + g, g)
                continue
            }
            var f = p.kind === 0 ? 0 : p.t / p.life
            var a = _level(f)
            for (var j = p.trail.length - 1; j >= 0; j--) {
                ctx.globalAlpha = a * (0.5 - j * 0.14)
                _dot(ctx, p.trail[j].x, p.trail[j].y, g)
            }
            ctx.globalAlpha = a
            _dot(ctx, p.x, p.y, g)
        }
        ctx.globalAlpha = 1
    }
}
