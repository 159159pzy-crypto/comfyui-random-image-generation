import pytest

from anima_webui.character_clothing_tags import is_character_clothing_tag


@pytest.mark.parametrize("tag", [
    "white shirt", "black skirt", "blue dress", "red coat", "long coat",
    "cropped jacket", "black sweater vest", "sailor dress", "school uniform",
    "garreg mach monastery uniform", "gem uniform (houseki no kuni)",
    "angel's 24 uniform (blue archive)", "black coat (kingdom hearts)",
    "hooded jacket", "hooded capelet", "white kimono", "hanfu", "sarong",
    "sundress", "miniskirt", "microdress", "gakuran", "cassock", "peplos",
    "white one-piece swimsuit", "side-tie bikini bottom", "bikini under clothes",
    "bodysuit", "leotard", "bustier", "lingerie", "bra", "bloomers",
    "underwear", "socks", "white socks", "stockings", "black thighhighs",
    "kneehighs", "thigh boots", "lace-up boots", "high heels", "sandals",
    "fingerless gloves", "single glove", "single elbow glove", "boxing gloves",
    "bridal gauntlets", "white mittens", "arm warmers", "leg warmers",
    "armor", "breastplate", "leg armor", "spiked pauldrons", "spacesuit",
])
def test_recognized_garments_footwear_and_gloves(tag):
    assert is_character_clothing_tag(tag)


@pytest.mark.parametrize("tag", [
    "long sleeves", "detached sleeves", "sleeveless", "sleeve cuffs",
    "ribbon-trimmed sleeves", "see-through sleeves", "single detached sleeve",
    "dress bow", "dress flower", "sleeve bow", "breast pocket", "lapels",
    "button gap", "buttons", "zipper", "zipper pull tab", "full-length zipper",
    "clothing cutout", "cleavage cutout", "navel cutout", "shoulder cutout",
    "underboob cutout", "thigh cutout", "stomach cutout", "armpit cutout",
    "sailor collar", "blue sailor collar", "frilled shirt collar",
    "mandarin collar", "high collar", "popped collar", "v-neck", "halterneck",
    "plunging neckline", "strapless", "spaghetti strap", "double-breasted",
])
def test_explicit_garment_construction_is_removed(tag):
    assert is_character_clothing_tag(tag)


@pytest.mark.parametrize("tag", [
    "no shirt", "no bra", "no panties", "no pants", "no shoes", "barefoot",
    "open jacket", "open shirt", "open fly", "partially unbuttoned",
    "partially unzipped", "unzipped", "sleeves rolled up", "strap slip",
    "shirt tucked in", "jacket on shoulders", "pants under skirt",
    "off shoulder", "wet shirt", "torn clothes", "bra peek", "panty peek",
])
def test_explicit_clothing_wearing_and_absence_states_are_removed(tag):
    assert is_character_clothing_tag(tag)


@pytest.mark.parametrize("tag", [
    "witch hat", "sailor hat", "mini top hat", "maid headdress", "headgear",
    "black headwear", "hood", "hood up", "hood down", "detached hood",
    "headwear with attached mittens", "visor (armor)", "helmet",
    "hair bow", "hair tubes", "hair ribbon", "frilled hairband", "hat bow",
    "glasses", "red-framed eyewear", "under-rim eyewear", "goggles on head",
    "necklace", "earrings", "bracelet", "navel piercing", "ribbon choker",
    "collar", "detached collar", "spiked collar", "lace collar", "fur collar",
    "necktie", "red bowtie", "neck ribbon", "scarf", "belt", "obi",
    "wrist cuffs", "armband", "wristband", "thigh strap", "shoulder bag",
])
def test_headwear_jewelry_and_standalone_accessories_are_preserved(tag):
    assert not is_character_clothing_tag(tag)


@pytest.mark.parametrize("tag", [
    "bare shoulders", "bare arms", "bare legs", "single bare shoulder",
    "cleavage", "navel", "covered navel", "collarbone", "midriff",
    "underboob", "sideboob", "pectoral cleavage", "armpits", "thighs",
    "thick thighs", "large breasts", "broad shoulders", "mole on thigh",
    "mole on collarbone", "shoulder tattoo", "navel hair", "bikini tan",
    "one-piece tan", "tanlines", "zettai ryouiki", "two-tone skin",
    "skin-covered horns", "dark-skinned female", "no humans", "no mouth",
])
def test_anatomy_exposure_and_skin_features_are_preserved(tag):
    assert not is_character_clothing_tag(tag)


@pytest.mark.parametrize("tag", [
    "red bow", "ribbon", "frills", "fur trim", "gold trim", "lace",
    "pom pom (clothes)", "buttons on hat", "button badge", "button eyes",
    "braid", "twin braids", "braided ponytail", "short hair", "short eyebrows",
    "mobile suit", "living clothes", "pokemon (creature)", "virtual youtuber",
    "hatsune miku", "saber (dress)", "glove girl", "coat of arms",
    "unreviewed ceremonial shirt", "unreviewed shoes", "novel uniform",
])
def test_ambiguous_decorations_identity_and_unknown_tags_are_preserved(tag):
    assert not is_character_clothing_tag(tag)


@pytest.mark.parametrize("tag", [
    "  WHITE_SHIRT  ", "WHITE__SHIRT", "White\t  Shirt", "white\nshirt",
    "OFF-SHOULDER_SHIRT", "  gem_uniform_(houseki_no_kuni)  ",
    "NO_BRA", "SLEEVES_ROLLED_UP",
])
def test_complete_tags_allow_case_space_and_underscore_variants(tag):
    assert is_character_clothing_tag(tag)


@pytest.mark.parametrize("tag", [
    "", "  ", "white shirt, blue eyes", "white shirt with hair bow",
    "(white shirt:1.2)", "[white shirt]", "{white shirt}", "white shirt*",
    "_raw_:white shirt", "off shoulder shirt", "t shirt",
])
def test_prompt_fragments_and_unknown_syntax_are_not_inferred(tag):
    assert not is_character_clothing_tag(tag)


def test_mixed_official_features_keep_the_character_and_accessory_details():
    # Independent examples from actual character feature lists, not a copy of
    # the classification set. The consumer retains original spellings/order.
    features = [
        "1girl", "Aqua Eyes", "very_long_hair", "twintails", "hair ornament",
        "white_shirt", "Pleated Skirt", "detached sleeves", "black thighhighs",
        "headphones", "aqua necktie", "bare shoulders", "navel", "aqua nails",
        "neck ribbon", "shoulder tattoo", "mysterious future feature",
    ]
    retained = [tag for tag in features if not is_character_clothing_tag(tag)]
    assert retained == [
        "1girl", "Aqua Eyes", "very_long_hair", "twintails", "hair ornament",
        "headphones", "aqua necktie", "bare shoulders", "navel", "aqua nails",
        "neck ribbon", "shoulder tattoo", "mysterious future feature",
    ]
