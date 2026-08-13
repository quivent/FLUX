import pathlib
import random

import era
import language


HERE = pathlib.Path(__file__).resolve().parent


def test_garden_era_is_bounded_and_composable():
    spec = era.load("garden-remembers-machine", HERE.parent)
    rng = random.Random(12)
    style = era.new_style(rng, spec)
    sequence = language.new_sequence(rng, style=style)
    sequence = era.apply_sequence(rng, sequence, spec)
    prompts = []
    for index in range(4):
        variation = language.variation(rng, sequence, index)
        variation = era.apply_variation(variation, spec, index)
        prompts.append(language.compose(variation))
    assert all(30 <= len(prompt.split()) <= 60 for prompt in prompts)
    assert all(variation in " ".join(prompts) for variation in spec["details"][:4])
    assert sequence["collection"] == "Images of Beauty"


def test_era_reference_cannot_be_an_empty_proposition(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{}")
    try:
        era.load(path)
    except ValueError as exc:
        assert "missing id" in str(exc)
    else:
        raise AssertionError("invalid era was accepted")
