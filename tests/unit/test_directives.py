from render_agent.util.directives import night_requested, parse_directives

WORDS = ["night view", "night render"]


def test_parse_known_directives():
    d = parse_directives(
        "Please keep it bright.\n@night=true\n@provider=MuAPI\n@views=Front day, Rear day\n"
        "@max_images=6\n@dwellings=DWELLING 1\n@select entry_door=pivot, spotted gum\n@dry_run=yes"
    )
    assert d.night is True and d.provider == "muapi" and d.max_images == 6 and d.dry_run is True
    assert d.views == ["Front day", "Rear day"] and d.dwellings == ["DWELLING 1"]
    assert d.select == {"entry_door": "pivot, spotted gum"}
    assert d.ignored == []


def test_unknown_and_invalid_directives_are_listed():
    d = parse_directives("@colour=red\n@max_images=lots\n@night=maybe\n@ nonsense")
    assert len(d.ignored) == 4
    assert d.max_images is None and d.night is None


def test_night_plain_words_and_override():
    assert night_requested("Please include night views of the front.", WORDS)
    assert not night_requested("Daytime only.", WORDS)
    assert not night_requested("include night views\n@night=false", WORDS)
    assert night_requested("@night=true", WORDS)
