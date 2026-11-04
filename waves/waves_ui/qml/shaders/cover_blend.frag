#version 440
// A playing cover's glow: the cover's own colors melted into one soft glow.
//
// The glow's shape is fixed, an even Gaussian falloff out from the cover's
// rounded edge, so it never reads as a light going round. Only the colors
// inside it move: each color is two wide pools on slow paths, weighted by
// how much of the cover it takes, and every pool overlaps the others, so
// the glow is all blend with no area of one flat color.
//
// Compile after any change (Qt's qsb, from PySide6):
//   qsb --glsl "100 es,120,150" --hlsl 50 --msl 12 -o cover_blend.frag.qsb cover_blend.frag
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float time;        // seconds, slowed to a stop on pause
    float spread;      // the falloff's width, px
    vec2 size;         // this quad, px
    vec2 cover;        // the cover, px
    float radius;      // the cover's corner, px
    float count;       // colors in use, 1..5
    vec4 c0;
    vec4 c1;
    vec4 c2;
    vec4 c3;
    vec4 c4;
    vec4 w;            // shares of c0..c3
    float w4;          // share of c4
};

float sdRound(vec2 p, vec2 b, float r) {
    vec2 q = abs(p) - b + r;
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}

vec3 well(vec2 u, float i, vec3 c, float wi, inout float ks) {
    float ph = i * 2.39996;
    vec2 at = 0.38 * vec2(sin(time * (0.09 + 0.023 * i) + ph),
                          cos(time * (0.071 + 0.019 * i) + ph * 1.7));
    float sig = 0.42 + 0.2 * sqrt(wi);
    float k = (0.35 + wi) * exp(-dot(u - at, u - at) / (sig * sig));
    ks += k;
    return c * k;
}

vec3 pools(vec2 u, float n, vec3 c, float wi, inout float ks) {
    return well(u, n, c, wi, ks) + well(u, n + 5.0, c, wi, ks);
}

void main() {
    vec2 p = (qt_TexCoord0 - 0.5) * size;
    float d = max(sdRound(p, cover * 0.5, radius), 0.0);
    float a = exp(-(d * d) / (2.0 * spread * spread));
    // Fade out before the quad's edge, so no falloff is ever cut square.
    vec2 e = 0.5 * size - abs(p);
    a *= smoothstep(0.0, 6.0, min(e.x, e.y));

    vec2 u = p / max(cover.x, cover.y);   // the cover's edge at +-0.5
    float ks = 0.0;
    vec3 acc = pools(u, 0.0, c0.rgb, w.x, ks);
    if (count > 1.5) acc += pools(u, 1.0, c1.rgb, w.y, ks);
    if (count > 2.5) acc += pools(u, 2.0, c2.rgb, w.z, ks);
    if (count > 3.5) acc += pools(u, 3.0, c3.rgb, w.w, ks);
    if (count > 4.5) acc += pools(u, 4.0, c4.rgb, w4, ks);
    vec3 col = (acc + c0.rgb * 0.02) / (ks + 0.02);
    // Lift saturation after the mix, so far-apart hues never meet in gray.
    float lum = dot(col, vec3(0.299, 0.587, 0.114));
    col = max(mix(vec3(lum), col, 1.25), 0.0);
    col *= 0.92 / max(max(max(col.r, col.g), col.b), 0.3);
    fragColor = vec4(col * a, a) * qt_Opacity;
}
