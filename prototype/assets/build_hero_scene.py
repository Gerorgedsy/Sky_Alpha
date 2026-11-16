"""Generate the hero illustration: an original, stylised night airfield.

Run `python3 prototype/assets/build_hero_scene.py` to rewrite
`hero-night-airfield.svg` next to this file. The seed keeps the skyline and
stars identical between runs; change it for a new arrangement.
"""
import random
from pathlib import Path
random.seed(42)
W, H = 1600, 1000
HZ = 640  # horizon
VX, VY = 1060, HZ + 4  # runway vanishing point
o = []
add = o.append
add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid slice">')
add('''<defs>
<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stop-color="#03070f"/><stop offset=".45" stop-color="#0a1628"/>
 <stop offset=".58" stop-color="#1a2240"/><stop offset=".64" stop-color="#3a2c3c"/>
</linearGradient>
<radialGradient id="cityglow" cx=".62" cy=".64" r=".55">
 <stop offset="0" stop-color="#f3a35d" stop-opacity=".38"/><stop offset=".45" stop-color="#b8603a" stop-opacity=".12"/>
 <stop offset="1" stop-color="#b8603a" stop-opacity="0"/>
</radialGradient>
<linearGradient id="ground" x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stop-color="#0d1322"/><stop offset="1" stop-color="#04070d"/>
</linearGradient>
<linearGradient id="runway" x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stop-color="#1a2232"/><stop offset="1" stop-color="#0f1520"/>
</linearGradient>
<radialGradient id="beam" cx="1" cy=".38" r="1">
 <stop offset="0" stop-color="#fff6dc" stop-opacity=".5"/><stop offset=".4" stop-color="#ffe7b0" stop-opacity=".12"/>
 <stop offset="1" stop-color="#ffe7b0" stop-opacity="0"/>
</radialGradient>
<radialGradient id="glow" r=".5"><stop offset="0" stop-color="#fff" stop-opacity=".9"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
<filter id="soft" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="14"/></filter>
<radialGradient id="sweep" cx="0" cy="0" r="1"><stop offset="0" stop-color="#7ee0d0" stop-opacity=".16"/><stop offset="1" stop-color="#7ee0d0" stop-opacity="0"/></radialGradient>
<linearGradient id="fade" x1="0" y1="0" x2="1" y2="0">
 <stop offset="0" stop-color="#03070f" stop-opacity=".82"/><stop offset=".5" stop-color="#03070f" stop-opacity=".25"/><stop offset="1" stop-color="#03070f" stop-opacity=".35"/>
</linearGradient>
<linearGradient id="bottom" x1="0" y1="0" x2="0" y2="1"><stop offset=".6" stop-color="#060d17" stop-opacity="0"/><stop offset="1" stop-color="#060d17"/></linearGradient>
</defs>
<style>
.tw{animation:tw 4s ease-in-out infinite}.tw2{animation:tw 6s ease-in-out 1.5s infinite}
@keyframes tw{0%,100%{opacity:.9}50%{opacity:.25}}
.strobe{animation:st 1.4s steps(1) infinite}@keyframes st{0%{opacity:1}8%{opacity:0}100%{opacity:0}}
.beacon{animation:bc 2s ease-in-out infinite}@keyframes bc{0%,100%{opacity:.15}50%{opacity:1}}
.rabbit{opacity:0;animation:rb 2s linear infinite}@keyframes rb{0%,4%{opacity:1}6%,100%{opacity:0}}
.plane{animation:fly 9s ease-in-out infinite alternate}@keyframes fly{from{transform:translate(0,0)}to{transform:translate(-26px,14px)}}
.radar{transform-origin:1230px 600px;animation:spin 14s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.tw,.tw2,.strobe,.beacon,.rabbit,.plane,.radar{animation:none}.rabbit{opacity:.6}}
</style>''')
add(f'<rect width="{W}" height="{H}" fill="url(#sky)"/>')
# stars
for i in range(170):
    x, y = random.uniform(0, W), random.uniform(0, HZ - 120) ** 1.0
    r = random.choice([.6, .7, .8, 1, 1.2, 1.5])
    cls = random.choice(['', '', 'tw', 'tw2'])
    add(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{r}" fill="#dfe9ff" opacity="{random.uniform(.25,.9):.2f}"{f" class={chr(34)}{cls}{chr(34)}" if cls else ""}/>')
add(f'<rect width="{W}" height="{H}" fill="url(#cityglow)"/>')
# radar rings
for r in (180, 300, 430, 570, 720):
    add(f'<circle cx="1230" cy="600" r="{r}" fill="none" stroke="#9fd8e6" stroke-opacity=".07" stroke-width="1.2"/>')
add('<g class="radar"><path d="M1230 600 L1230 -120 A720 720 0 0 1 1735 90 Z" fill="url(#sweep)" filter="url(#soft)" opacity=".55"/></g>')
# distant skyline
x = 380
def building(x, w, h, tone):
    add(f'<rect x="{x}" y="{HZ-h}" width="{w}" height="{h}" fill="{tone}"/>')
    for wy in range(int(HZ - h + 6), HZ - 4, 7):
        for wx in range(int(x + 3), int(x + w - 3), 6):
            if random.random() < .33:
                c = random.choice(['#ffd9a0', '#ffe9c4', '#cfe3ff', '#ffc27a'])
                add(f'<rect x="{wx}" y="{wy}" width="2.4" height="3" fill="{c}" opacity="{random.uniform(.35,.9):.2f}"/>')
while x < 1600:
    w = random.randint(14, 42)
    h = random.randint(28, 120)
    if 900 < x < 960: h = random.randint(150, 200)
    building(x, w, h, random.choice(['#0b1222', '#0d1526', '#0a101d']))
    x += w + random.randint(0, 6)
# spire tower
add(f'<path d="M1180 {HZ} L1186 {HZ-250} L1203 {HZ-250} L1209 {HZ} Z" fill="#0c1424"/><rect x="1193.5" y="{HZ-318}" width="2" height="70" fill="#0c1424"/><circle cx="1194.5" cy="{HZ-320}" r="2.2" fill="#ff5a4a" class="beacon"/>')
for wy in range(HZ - 244, HZ - 6, 8):
    for wx in (1188, 1194, 1200):
        if random.random() < .5:
            add(f'<rect x="{wx}" y="{wy}" width="2.5" height="3" fill="#cfe3ff" opacity="{random.uniform(.3,.85):.2f}"/>')
# ground
add(f'<rect y="{HZ}" width="{W}" height="{H-HZ}" fill="url(#ground)"/>')
# runway trapezoid
bl, br = 520, 1600 + 250
add(f'<path d="M{VX-14} {VY} L{VX+14} {VY} L{br} {H} L{bl} {H} Z" fill="url(#runway)"/>')
def lerp(a, b, t): return a + (b - a) * t
# centerline dashes and edge lights
n = 26
for i in range(n):
    t0 = (i / n) ** 2.1; t1 = ((i + .45) / n) ** 2.1
    y0, y1 = lerp(VY, H, t0), lerp(VY, H, t1)
    cx0, cx1 = lerp(VX, (bl + br) / 2, t0), lerp(VX, (bl + br) / 2, t1)
    wdt0, wdt1 = lerp(.6, 9, t0), lerp(.6, 9, t1)
    add(f'<path d="M{cx0-wdt0/2:.1f} {y0:.1f} L{cx0+wdt0/2:.1f} {y0:.1f} L{cx1+wdt1/2:.1f} {y1:.1f} L{cx1-wdt1/2:.1f} {y1:.1f} Z" fill="#c9d3e0" opacity=".45"/>')
for i in range(34):
    t = (i / 34) ** 2.0
    y = lerp(VY, H, t)
    r = lerp(.7, 5.5, t)
    for side, xe in ((0, lerp(VX - 14, bl, t)), (1, lerp(VX + 14, br, t))):
        col = '#fff3d6' if i > 6 else '#ffc76a'
        add(f'<circle cx="{xe:.1f}" cy="{y:.1f}" r="{r*2.4:.1f}" fill="url(#glow)" opacity=".35"/><circle cx="{xe:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{col}"/>')
# threshold greens near horizon end + taxiway blues
for k in range(9):
    add(f'<circle cx="{VX-16+k*4:.0f}" cy="{VY-1}" r="1.1" fill="#6dff9e"/>')
for i in range(16):
    t = (i / 16) ** 1.8
    y = lerp(HZ + 18, H - 40, t)
    xb = lerp(880, 120, t)
    add(f'<circle cx="{xb:.0f}" cy="{y:.0f}" r="{lerp(.8,4,t):.1f}" fill="#4f8dff" opacity=".9"/><circle cx="{xb:.0f}" cy="{y:.0f}" r="{lerp(2,10,t):.1f}" fill="#4f8dff" opacity=".12"/>')
# approach lights rabbit (beyond threshold, toward viewer we can't; place before VP far end) - put along centerline near horizon
for k in range(7):
    t = (k / 40)
    y = VY - 2 - k * 1.2
    add(f'<circle cx="{VX}" cy="{y:.1f}" r="1.6" fill="#ffffff" class="rabbit" style="animation-delay:{(6-k)*0.08:.2f}s"/>')
# control tower
tx, ty = 300, HZ
add(f'<path d="M{tx-10} {ty} L{tx-6} {ty-150} L{tx+6} {ty-150} L{tx+10} {ty} Z" fill="#0b1220"/>')
add(f'<path d="M{tx-26} {ty-150} L{tx+26} {ty-150} L{tx+20} {ty-178} L{tx-20} {ty-178} Z" fill="#5fd0c5" opacity=".55"/>')
add(f'<rect x="{tx-28}" y="{ty-184}" width="56" height="7" rx="2" fill="#0b1220"/><rect x="{tx-1}" y="{ty-212}" width="2" height="28" fill="#0b1220"/><circle cx="{tx}" cy="{ty-214}" r="2.4" fill="#ff5a4a" class="beacon"/>')
# hangars
add(f'<path d="M80 {HZ} Q150 {HZ-46} 220 {HZ} Z" fill="#0a101c"/><path d="M200 {HZ} Q250 {HZ-30} 300 {HZ} Z" fill="#0b1220"/>')
# aircraft on approach: top-down airliner, nose to the left
add('<g class="plane"><g transform="translate(1010 240) rotate(-14) scale(1.15)">')
add('<path d="M-120 0 L-560 -70 L-560 110 Z" fill="url(#beam)" filter="url(#soft)" opacity=".85"/>')
add('''<g fill="#18223a" stroke="#3a4d70" stroke-width="1.1" stroke-linejoin="round">
 <path d="M-12 -10 L40 -118 L62 -118 L40 -10 Z"/><path d="M-12 10 L40 118 L62 118 L40 10 Z"/>
 <path d="M112 -6 L136 -48 L150 -48 L140 -6 Z"/><path d="M112 6 L136 48 L150 48 L140 6 Z"/>
 <path d="M-122 0 C-120 -9 -106 -13 -86 -13 L140 -9 C152 -8 160 -4 162 0 C160 4 152 8 140 9 L-86 13 C-106 13 -120 9 -122 0 Z"/>
 <rect x="-2" y="-62" width="34" height="11" rx="5.5"/><rect x="-2" y="51" width="34" height="11" rx="5.5"/>
</g>''')
add('<path d="M-104 -6 C-96 -10 -88 -10 -84 -8" stroke="#8fb8ff" stroke-opacity=".5" stroke-width="2" fill="none"/>')
add('<circle cx="-122" cy="0" r="3.4" fill="#fff8e6"/><circle cx="-122" cy="0" r="18" fill="url(#glow)" opacity=".75"/>')
add('<circle cx="50" cy="-118" r="2.6" fill="#ff4d4d"/><circle cx="50" cy="118" r="2.6" fill="#41f28a"/>')
add('<circle cx="50" cy="-118" r="11" fill="url(#glow)" class="strobe"/><circle cx="50" cy="118" r="11" fill="url(#glow)" class="strobe"/><circle cx="150" cy="0" r="2.2" fill="#ff4d4d" class="beacon"/>')
add('</g></g>')
# second distant aircraft
add('<g transform="translate(560 170)"><circle r="1.6" fill="#fff"/><circle r="6" fill="url(#glow)" class="strobe" style="animation-delay:.6s"/><circle cx="6" cy="1" r="1.1" fill="#ff4d4d" class="beacon"/></g>')
add(f'<rect width="{W}" height="{H}" fill="url(#fade)"/><rect width="{W}" height="{H}" fill="url(#bottom)"/>')
add('</svg>')
open(Path(__file__).with_name('hero-night-airfield.svg'), 'w', encoding='utf-8').write('\n'.join(o))
print("Wrote hero-night-airfield.svg")
