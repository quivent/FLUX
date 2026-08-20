package prompt

import (
	"fmt"
	"strings"
)

type Shape struct {
	Style    string
	Mood     string
	Camera   string
	Light    string
	Palette  string
	Texture  string
	Detail   string
	Chaos    string
	Director string
	Preset   string
}

type Ratio struct {
	Name   string
	Width  int
	Height int
}

type Definition struct {
	Name string
	Text string
}

type Preset struct {
	Name     string
	Style    string
	Mood     string
	Ratio    string
	Steps    int
	Guidance float64
	Note     string
}

var OrderedStyles = []Definition{
	{"cinema", "cinematic lighting, expressive composition, high dynamic range"},
	{"product", "premium product photography, precise materials, clean reflections"},
	{"editorial", "editorial image, confident framing, natural imperfections"},
	{"architect", "architectural visualization, balanced geometry, tactile surfaces"},
	{"document", "documentary realism, available light, lived-in detail"},
	{"speculative", "near-future industrial design, plausible materials, restrained worldbuilding"},
	{"material", "macro material study, exact surface response, visible fabrication detail"},
	{"anime", "anime production still, expressive linework, cinematic color design, clean compositing"},
	{"noir", "graphic noir lighting, hard silhouettes, controlled negative space"},
	{"arcane", "Fortiche Arcane production style, visible oil and gouache brushwork, graphic angular plane rendering, sharp stylized anatomy, 3D-2D hybrid aesthetic"},
	{"fortiche", "Fortiche animation key visual, textured digital matte painting, dramatic theatrical chiaroscuro, stylized facial geometry, textured paint layering"},
}

var OrderedMoods = []Definition{
	{"quiet", "quiet atmosphere, spare composition, soft tension"},
	{"electric", "electric energy, sharp contrast, saturated accents"},
	{"clinical", "clinical precision, neutral light, exact detail"},
	{"warm", "warm practical light, human scale, inviting texture"},
	{"ominous", "subtle unease, low contrast shadows, restrained drama"},
	{"optimistic", "clear air, crisp highlights, forward-looking tone"},
	{"nocturne", "night air, restrained highlights, deep color separation"},
	{"melancholy", "melancholy calm, suspended motion, soft emotional distance"},
	{"fever", "high-intensity color pressure, kinetic framing, unstable atmosphere"},
}

var OrderedCameras = []Definition{
	{"wide", "wide establishing frame, clear spatial read, foreground-middle-background depth"},
	{"close", "intimate close framing, tactile subject presence, compressed emotional focus"},
	{"macro", "macro lens behavior, shallow depth of field, surface-level inspection"},
	{"low", "low camera angle, monumental silhouette, strong vertical pressure"},
	{"overhead", "overhead composition, graphic layout, readable geometry"},
	{"tracking", "tracking-shot energy, lateral motion, cinematic parallax"},
	{"portrait", "portrait lens compression, graceful falloff, face and gesture priority"},
}

var OrderedLights = []Definition{
	{"golden", "golden-hour light, long warm shadows, gentle atmospheric haze"},
	{"neon", "neon practicals, colored bounce light, luminous edges"},
	{"overcast", "soft overcast light, broad tonal range, subtle color separation"},
	{"rim", "strong rim light, readable silhouette, dark-to-light edge contrast"},
	{"lantern", "warm lantern light, localized glow, deep surrounding shadow"},
	{"storm", "storm light, charged sky, intermittent highlights, heavy atmosphere"},
	{"studio", "controlled studio cross-light, clean highlights, precise material response"},
	{"hextech", "pulsing hextech crystal luminescence, cyan edge halation, harsh geometric shadows"},
	{"chemtech", "toxic emerald underlighting, sickly lime haze, caustic industrial spill"},
}

var OrderedPalettes = []Definition{
	{"sakura", "sakura pink accents, warm ivory highlights, restrained ink-dark contrast"},
	{"verdant", "moss green, weathered stone, soft cream, natural muted contrast"},
	{"cobalt", "cobalt blue shadows, pale cyan highlights, controlled cool spectrum"},
	{"ember", "ember orange accents, charcoal darks, smoke-soft transitions"},
	{"mono", "near-monochrome palette, tonal discipline, small color interruption"},
	{"pastel", "pastel production colors, gentle gradients, airy value structure"},
	{"acid", "acid accent colors, unstable contrast, sharp synthetic highlights"},
	{"hextech", "hextech cyan glow, gilded gold filigree, deep navy shadows, polished copper highlights"},
	{"zaun", "toxic emerald chemtech mist, rusted umber iron, violet neon underglow, radioactive chartreuse"},
	{"piltover", "sun-bleached white marble, warm brass trim, sapphire glass reflections, crisp ivory air"},
}

var OrderedTextures = []Definition{
	{"film", "fine film grain, optical softness, natural halation"},
	{"ink", "ink-wash edges, brush variation, handmade line character"},
	{"cel", "painted cel texture, clean fills, subtle registration artifacts"},
	{"paper", "toothed paper grain, pigment pooling, visible fiber"},
	{"metal", "brushed metal, micro-scratches, anisotropic reflections"},
	{"glass", "layered glass, caustic reflections, transparent edge detail"},
	{"weathered", "weathered surfaces, patina, wear patterns, lived-in imperfection"},
	{"gouache", "hand-painted gouache impasto, dry-brush edge breaks, matte pigment pooling, visible brushstroke layering"},
}

var OrderedDetails = []Definition{
	{"minimal", "minimal detail density, disciplined negative space, clean read"},
	{"balanced", "balanced detail density, clear focal hierarchy, no clutter"},
	{"dense", "dense environmental detail, layered props, secondary story cues"},
	{"ornate", "ornate decorative structure, intricate motif repetition, crafted surfaces"},
	{"diagram", "diagrammatic clarity, legible construction logic, annotated-feeling precision"},
}

var OrderedChaos = []Definition{
	{"calm", "stable composition, restrained variation, low visual noise"},
	{"alive", "organic asymmetry, lived-in irregularity, natural imperfection"},
	{"wild", "unpredictable shapes, kinetic composition, expressive exaggeration"},
	{"surreal", "surreal juxtaposition, dream logic, impossible but coherent spatial cues"},
	{"maximal", "maximal visual pressure, layered spectacle, controlled overload"},
}

var OrderedDirectors = []Definition{
	{"miyazaki", "lyrical environmental storytelling, gentle wonder, hand-crafted world detail"},
	{"kon", "psychological transitions, layered reality, precise graphic match cuts"},
	{"oshii", "philosophical stillness, urban melancholy, reflective surfaces"},
	{"watanabe", "cool rhythmic framing, jazz-inflected motion, lived-in futurism"},
	{"anno", "monumental framing, anxious scale, stark graphic contrast"},
	{"shinkai", "luminous skies, emotional weather, polished atmospheric light"},
	{"vogue", "high-fashion editorial staging, confident pose language, refined styling"},
	{"brutalist", "severe massing, concrete tactility, institutional geometry"},
	{"fortiche", "Fortiche visual rhythm, dynamic angular perspective, expressive painterly character planes, graphic rim light"},
}

var OrderedRatios = []Ratio{
	{"square", 1024, 1024},
	{"wide", 1344, 768},
	{"portrait", 768, 1344},
	{"fourthree", 1152, 864},
	{"draft", 768, 768},
	{"poster", 896, 1344},
	{"banner", 1536, 640},
}

var OrderedPresets = []Preset{
	{"sketch", "document", "quiet", "draft", 14, 3.0, "fast composition pass"},
	{"hero", "cinema", "electric", "wide", 30, 3.5, "wide dramatic hero image"},
	{"object", "product", "clinical", "square", 28, 3.2, "inspection-friendly product shot"},
	{"space", "architect", "warm", "fourthree", 30, 3.4, "interior or architectural concept"},
	{"cover", "editorial", "ominous", "poster", 32, 3.6, "vertical magazine-cover energy"},
	{"future", "speculative", "optimistic", "wide", 30, 3.4, "plausible near-future scene"},
	{"anime", "anime", "melancholy", "poster", 30, 3.5, "anime key visual or production still"},
	{"noir", "noir", "nocturne", "wide", 30, 3.4, "graphic shadow-forward scene"},
	{"arcane-hero", "arcane", "electric", "poster", 30, 3.5, "Fortiche-grade Arcane hero character visual"},
	{"arcane-zaun", "arcane", "ominous", "wide", 30, 3.6, "Zaun undercity chemtech atmosphere"},
	{"arcane-piltover", "fortiche", "optimistic", "wide", 30, 3.5, "Piltover gilded architectural grandeur"},
	{"arcane-turn", "arcane", "clinical", "square", 28, 3.5, "64-frame character turnaround cell"},
	{"arcane-jinx", "arcane", "fever", "poster", 30, 3.5, "chaotic loose cannon, electric blue braids, neon graffiti haze, gouache brushwork"},
	{"arcane-vi", "arcane", "electric", "poster", 30, 3.5, "athletic underground brawler, hydraulic gauntlets, pink undercut, amber chiaroscuro"},
	{"arcane-viktor", "arcane", "melancholy", "portrait", 30, 3.4, "brilliant frail alchemist, glowing violet hexcore cane, textured oil impasto"},
	{"arcane-jayce", "fortiche", "optimistic", "wide", 30, 3.5, "gilded high defender, hextech warhammer, gold-trimmed white armor, radiant rim light"},
	{"arcane-silco", "arcane", "nocturne", "portrait", 32, 3.6, "scarred crime lord, glowing orange prosthetic eye, toxic emerald shadows"},
	{"arcane-ekko", "arcane", "electric", "wide", 30, 3.5, "firelight leader, hourglass face paint, kinetic hoverboard action, vibrant neon rim"},
}

func Compose(base string, s Shape) (string, error) {
	if _, err := PresetByName(s.Preset); s.Preset != "" && err != nil {
		return "", err
	}
	checks := []struct {
		defs []Definition
		name string
	}{
		{OrderedStyles, s.Style},
		{OrderedMoods, s.Mood},
		{OrderedCameras, s.Camera},
		{OrderedLights, s.Light},
		{OrderedPalettes, s.Palette},
		{OrderedTextures, s.Texture},
		{OrderedDetails, s.Detail},
		{OrderedChaos, s.Chaos},
		{OrderedDirectors, s.Director},
	}
	for _, check := range checks {
		if _, err := definitionByName(check.defs, check.name); check.name != "" && err != nil {
			return "", err
		}
	}
	parts := []string{strings.TrimSpace(base)}
	for _, item := range checks {
		if v := TextFor(item.defs, item.name); v != "" {
			parts = append(parts, v)
		}
	}
	return strings.Join(parts, ", "), nil
}

func RatioByName(name string) (Ratio, error) {
	if name == "" {
		name = "square"
	}
	for _, r := range OrderedRatios {
		if r.Name == name {
			return r, nil
		}
	}
	return Ratio{}, fmt.Errorf("unknown ratio %q", name)
}

func PresetByName(name string) (Preset, error) {
	if name == "" {
		return Preset{}, nil
	}
	for _, p := range OrderedPresets {
		if p.Name == name {
			return p, nil
		}
	}
	return Preset{}, fmt.Errorf("unknown preset %q", name)
}

func TextFor(defs []Definition, name string) string {
	d, err := definitionByName(defs, name)
	if err != nil {
		return ""
	}
	return d.Text
}

func definitionByName(defs []Definition, name string) (Definition, error) {
	if name == "" {
		return Definition{}, nil
	}
	for _, d := range defs {
		if d.Name == name {
			return d, nil
		}
	}
	return Definition{}, fmt.Errorf("unknown prompt lens %q", name)
}

func Sparks(base string) []string {
	base = strings.TrimSpace(base)
	if base == "" {
		base = "untitled subject"
	}
	return []string{
		base + ", seen as a precise material prototype, visible seams, studio cross-light",
		base + ", photographed as an editorial cover, negative space, decisive silhouette",
		base + ", translated into a quiet architectural scene, human scale, soft daylight",
		base + ", rendered as a field document, imperfect framing, useful real-world detail",
		base + ", imagined as a near-future object, plausible engineering, restrained color",
		base + ", staged as a cinematic keyframe, strong foreground shape, atmospheric depth",
	}
}

func RandomLensNames(seed int64, count int) []string {
	defs := [][]Definition{
		OrderedStyles,
		OrderedMoods,
		OrderedCameras,
		OrderedLights,
		OrderedPalettes,
		OrderedTextures,
		OrderedDetails,
		OrderedChaos,
		OrderedDirectors,
	}
	if count < 1 {
		count = 1
	}
	out := make([]string, 0, count)
	for i := 0; i < count; i++ {
		group := defs[int((seed+int64(i*7))%int64(len(defs)))]
		item := group[int((seed+int64(i*11))%int64(len(group)))]
		out = append(out, item.Name)
	}
	return out
}
