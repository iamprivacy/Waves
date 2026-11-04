#version 440
// A cover's face with rounded corners.
//
// Qt clips an item's children to its box, never to its radius, so an Image
// inside a rounded Rectangle shows square corners. This draws the Image's
// own texture instead (the Image stays, loaded and cached as before, at
// opacity 0), cropped the way PreserveAspectCrop crops it, and cut to the
// Rectangle's rounded shape with one pixel of antialiasing. Nothing is
// rendered to a texture first: no layer, no framebuffer per cover.
//
// Compile after any change (Qt's qsb, from PySide6):
//   qsb --glsl "100 es,120,150" --hlsl 50 --msl 12 -o cover_round.frag.qsb cover_round.frag
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 size;         // this item, px
    vec2 tex;          // the source image, px (only its aspect matters)
    float radius;      // the corner, px
    float shadeH;      // a bottom shade's height, px (0 = none)
    float shadeA;      // its strength at the bottom edge, 0..1
};
layout(binding = 1) uniform sampler2D src;

float sdRound(vec2 p, vec2 b, float r) {
    vec2 q = abs(p) - b + r;
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}

void main() {
    vec2 p = (qt_TexCoord0 - 0.5) * size;
    // PreserveAspectCrop: the image scaled to cover the item, centered.
    vec2 shown = tex * max(size.x / tex.x, size.y / tex.y);
    vec4 c = texture(src, 0.5 + p / shown);
    float d = sdRound(p, 0.5 * size, radius);
    float a = 1.0 - smoothstep(-0.7, 0.7, d);
    // The bottom shade, toward #0b0d10, inside the face's own edge.
    float t = shadeH > 0.0 ? clamp((p.y - (0.5 * size.y - shadeH)) / shadeH, 0.0, 1.0) : 0.0;
    t = t * t * (3.0 - 2.0 * t);   // eased in, so its top draws no line
    c.rgb = mix(c.rgb, vec3(0.043, 0.051, 0.063) * c.a, t * shadeA);
    fragColor = c * a * qt_Opacity;
}
