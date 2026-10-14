from tests.grade_extraction import grade

KEY = {
    "fixture": "x",
    "external": {"roof": "COLORBOND ROOF ON 22.5° PITCH", "eaves": "500 EAVES", "door": "PIVOT DOOR"},
    "site": {"area": "769", "tank": "3000 LITRE TANK"},
    "dwellings": {"relationships": ["D1 mirrors D2"], "kitchen_label_present": False},
    "expected_contradictions": ["Elevation draws a 4-tier awning window; plan labels it 21-12 awn"],
    "must_not_appear": ["swimming pool", "alfresco"],
}
PAGES = {1: "SITE AREA 769m²  3000 LITRE TANK  ALFRESCO"}


def test_good_spec():
    spec = {
        "EXTERNAL": {
            "roof": "Colorbond roof on 22.5 degrees pitch",
            "eaves": "500 eaves",
            "door": "PIVOT DOOR",
        },
        "SITE": {"total": "769 m²", "tank": "3000 litre tank"},
        "UNCERTAINTIES": ["Elevation shows a 4-tier awning window but the plan label is 21-12 awn"],
    }
    r = grade(spec, KEY, PAGES)
    assert r.count("matched") == 5 and r.count("manual") == 2
    assert not r.hard_failures
    assert [i.status for i in r.forbidden] == ["absent", "absent"]


def test_bad_spec_is_triaged_not_just_failed():
    spec = {
        "EXTERNAL": {"roof": "tile roof", "door": "pivot entry door"},
        "SITE": {"pool": "swimming pool", "deck": "alfresco"},
        "UNCERTAINTIES": [],
    }
    r = grade(spec, KEY, PAGES)
    by_path = {i.path: i.status for i in r.items}
    assert by_path["site.tank"] == "model_missed"  # printed on the PDF, spec lacks it
    assert by_path["external.eaves"] == "key_unverified"  # neither in spec nor PDF text: check the key
    assert by_path["external.door"] == "near"
    forbidden = {i.value: i.status for i in r.forbidden}
    assert forbidden == {"swimming pool": "invented", "alfresco": "in_pdf"}  # alfresco is on the drawings
    assert r.contradictions[0].status == "not_flagged"
    assert len(r.hard_failures) == 2
    assert "key_unverified" in r.to_markdown()
