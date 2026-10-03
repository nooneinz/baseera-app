# Design
<!-- impeccable:design-schema 1 -->

The system follows the "Halo" editorial template, re-skinned in Baseera's identity. Source of truth in code:
`dashboard/static/dashboard/css/halo.css` (tokens + components) and `dashboard/static/dashboard/js/halo.js` (motion).

## Colors
**Core palette is fixed. Do not change it or introduce another accent.** The template's orange is replaced by Glow.
- **Nile (primary dark):** `#2b2470`
- **Glow (the single accent):** `#7c6cf0`
- **Lavender (soft accent):** `#b9a6f2`
- **Accent 4:** `#4a3f8a` (accent used as TEXT on light surfaces)
- **Ink (the template's black, deep navy):** `#0d0a2b`, panels `#15104a`
- Light: bg `#ffffff`, surface `#f4f3fa`, muted text `#5f5b7a`, hairline `#e4e2ee`. Dark: bg `#0b0826`, surface `#15104a`.

Contrast rules (checked): ink on white 17:1, muted on white 6.4:1, **Glow buttons use ink text, never white text** (white on Glow is 3.99:1), white on Accent 4 is 8.9:1.
Accent usage: one fill per screen (primary CTA, the highlighted word of a headline, the highlighted row/number). Everything else is ink, surface gray, or hairline.

## Typography
- **Display (headings, big numbers):** Alexandria 800, tight leading (about 1.15 Arabic, 0.95 Latin), no letter-spacing in Arabic. English display headings are uppercase with -0.035em tracking.
- **Body and UI:** IBM Plex Sans Arabic 300-700.
- Headline scale is huge on purpose: `clamp(2.4rem, 7vw, 6rem)` for section titles, with a small 22rem side paragraph opposite it.

## Layout and surfaces
- Flat. No glass, no blur, no colored shadows. Surfaces are gray panels or the ink panel with 4px corners (`--hw-r`).
- Structure per section: giant title on one side, short paragraph on the other, then hairline rows, an accordion, or a 2-3 column panel grid.
- Hairline rules (`--hw-hair`) separate rows; no card borders beyond that.
- RTL by default; use logical properties (`inset-inline-*`, `padding-inline-*`).
- Little text: one clear number or visual per block, sample figures are labelled "illustrative example".

## Motion (halo.js, all disabled under prefers-reduced-motion)
- **Reveal:** headings arrive faint and 28px low, settle in 0.9s (`cubic-bezier(.2,.7,.2,1)`); siblings stagger by 0.12s.
- **Header:** hides while scrolling down, returns while scrolling up, blurred backing after the first 24px.
- **Row wipe:** hovering a `.hw-row` fills it with ink from the inline-start edge; an inverted clone is revealed by `clip-path`, so the text flips exactly at the wipe edge.
- **Accordion:** `grid-template-rows 0fr -> 1fr`, plus/minus morph, one item open at a time.
- **Counters:** numbers count up once when seen.
- **Floating CTA:** appears after the first screen, dismissible for the session.
- Hero stage: data chips drift and converge into the Baseera lens; the logo is never altered.

## Rules
1. Never change the palette. No gradients except the existing brand gradient on legacy dashboard buttons.
2. No emoji. Icons are Lucide or inline SVG.
3. Real content only: no invented awards, partners, reviews or statistics. Sample numbers must be labelled.
4. The Baseera logo image is used as is.
5. Dashboard pages inherit the same tokens through `halo.css`; heavy motion is reserved for the landing page.

## Landing page: dark product look (`landing.css`, scoped under `.sc`)
Applies only to the home page; every other page keeps the Halo light system above.
- Black stage `#05030f`, violet aurora (drifting blurred blobs plus two slowly rotating glowing arcs) behind the hero and the final CTA.
- Headlines are medium weight (Alexandria 500), centered, with ONE accent phrase in a calligraphic/serif face: Aref Ruqaa in Arabic, Instrument Serif italic in English (`.sc-accent`). The accent phrase is the only colored text in a headline (Lavender).
- Each section: small pill label, headline, one muted sentence, then content.
- Hero: badge with live dot, headline, two buttons, a row of the data sources Baseera reads (Excel, CSV, PDF, ledger photo, WhatsApp, Google Maps), then a glass dashboard mock (KPIs, cash-flow line that draws itself, agent list).
- Product section: two-column bento of six cards (leak detection, reports, agents, buyers from Google Maps, credit-risk warning, WhatsApp weekly pulse), each with a mini interface; alternate cards flip text above/below the mock; a violet spotlight follows the pointer.
- Accent button on black: Glow fill with ink text (contrast 4.8:1). Secondary: translucent white with a hairline.
- Mock interfaces are labelled as illustrative examples; no invented reviews, partners or ratings.

## Landing page v3: starfield SaaS start, thin type
- The page opens like a dark analytics-product template: faint twinkling starfield, a violet glow under the header, a badge, a two-line headline with a bright-to-soft gradient (lowest stop keeps 5:1 on black), a ghost button plus a glowing violet button, then two floating panels (an upload card and a tilted dashboard) with floating audience chips, and an "illustrative example" label.
- Next section: badge, gradient headline, one sentence, one button, then a three-up grid of cards. Each card is a mini interface (area chart, runway gauge, buyers list, reports, customer risk, WhatsApp), a title, one sentence and a ghost "Learn more" button.
- Type on the landing is thin, small and simple in Arabic and English: IBM Plex Sans Arabic 300 for everything (200 available), headlines `clamp(1.8rem, 3.6-4.2vw, 2.7-3.2rem)`, body .8-.9rem. No calligraphic or serif accent anymore; `.sc-accent` is just the dimmer second half of a headline.
- Heavy display weights (Alexandria 800) remain only on the light pages.

## Landing page v4: monochrome (supersedes the violet-aurora looks above)
Home page only, scoped under `.sc` in `landing.css`. Reference mood: a dark marketing site with silver arcs, frosted glass and white buttons.
- **Palette:** near-black `#060608` with neutral grays (`#121214`, `#18181b`, `#2a2a2e`), text `#f4f4f6`, muted `#a1a1aa` (7.6:1). Primary button is WHITE with ink text; secondary is translucent with a hairline. Brand violet `#7c6cf0` survives only as the live dot; the logo image is untouched.
- **Hero:** badge, two-line gradient headline (white to 66%, second line 72% to 50% so it stays above 4.5:1), white button, two huge silver crescents (CSS spheres with a bright rim) framing the headline, then an app window (icon rail, filters, three KPIs that count up, a line that draws itself) faded at the bottom.
- **Services:** large thin start-aligned heading with round prev/next arrows and a scroll-snap carousel of six cards (icon in a circle, title, sentence, "Learn more").
- **Agents:** vertical tab list, a visual panel (white icon disc with rings) and the agent text with pills; arrow keys move between tabs.
- **Pricing:** a giant word fading out behind three frosted-glass cards (blur 22px, 28px radius), check-in-circle lists, pill buttons; the middle plan has the white button. No yearly toggle (there is no yearly price).
- **Voices:** heading and hairline on one side, three stacked quote cards on the other (customer logo, a neutral person icon, an outcomes list), plus a faint ribbon curve behind. No invented photos or people.
- **Method film:** recolored from indigo to the same grays.
- **Type:** IBM Plex Sans Arabic 300, small (body .8-.9rem); section headings `clamp(1.9rem, 4.4vw, 3.4rem)`, uppercase in English only.
