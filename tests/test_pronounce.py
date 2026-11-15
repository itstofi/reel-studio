"""Pronunciation QA: what the voice said vs the script the viewer reads."""
from types import SimpleNamespace as W

from reelstudio.voice.pronounce import check, respell, tokens


def heard(text: str):
    return [W(text=t, start=float(i)) for i, t in enumerate(text.split())]


def kinds(script, said):
    return [(p.kind, p.word) for p in check(script, heard(said)).problems]


def test_clean_line_passes():
    assert check("This is Sara. She has a small bakery.", heard("This is Sarah. She has a small bakery.")).ok


def test_ai_heard_as_i_or_eye_is_caught():
    assert kinds("One night, she asks AI: write a post.", "One night she asks I, write a post.") == [("misheard", "AI:")]
    assert ("misheard", "AI") in kinds("So Sara opens AI again.", "So Sarah opens the eye again.")


def test_repeat_is_caught():
    assert kinds("and how the answer should look.", "and how the answer should look. look.") == [("repeat", "look.")]


def test_missing_word_is_caught():
    assert kinds("The FAA stopped every plane.", "The FAA stopped plane.") == [("missing", "every")]


def test_numbers_and_spelled_acronyms_match():
    assert check("More than 4,700 flights were cancelled.", heard("More than 4700 flights were canceled.")).ok
    assert check("The FAA said 1.5 million people.", heard("The F. A. A. said 1.5 million people.")).ok
    assert check("It lasted four hours and twenty one minutes.", heard("It lasted 4 hours and 21 minutes.")).ok
    assert check("At 3:54 p.m.", heard("At 3.54 PM.")).ok


def test_tokens_join_letters_not_words():
    assert [t for t, _ in tokens("the U.S. and a plane".split())] == ["the", "us", "and", "a", "plane"]


def test_respell_keeps_punctuation_and_skips_pronoun_us():
    assert respell("She asks AI: write.") == "She asks A.I.: write."
    assert respell("It will take us time, says the FAA.") == "It will take us time, says the F.A.A."


def test_whisper_split_numbers_are_glued():
    split = [W(text=t, start=float(i)) for i, t in enumerate(["more", "than", "10", ",000", "were", "delayed."])]
    assert check("more than 10,000 were delayed.", split).ok
    wrong = [W(text=t, start=float(i)) for i, t in enumerate(["more", "than", "4", ",100", "flights"])]
    assert [p.kind for p in check("more than 4,700 flights", wrong).problems] == ["misheard"]


def test_dollar_sign_equals_dollars():
    assert check("leaders want 50 billion dollars from Congress", heard("leaders want $50 billion from Congress")).ok
    assert check("it costs $5 now", heard("it costs 5 dollars now")).ok


def test_invented_word_at_line_edges_is_caught():
    assert [p.kind for p in check("The network broke down.", heard("Sad, the network broke down.")).problems] == ["extra"]
    assert [p.kind for p in check("The network broke down.", heard("The network broke down. Thanks")).problems] == ["extra"]
    assert check("The network broke down.", heard("The network, uh, broke down.")).ok   # one mid-line word = noise


def test_split_compounds_match():
    assert check("It was not a cyberattack.", heard("It was not a cyber attack.")).ok
    assert check("Open your e mail now.", heard("Open your email now.")).ok
    assert not check("It was not a cyberattack.", heard("It was not a attack.")).ok


def test_arabic_numbers_brands_and_dialect_particles():
    assert check("وقف بأكثر من سبعين دولة", heard("وقف بأكثر من 70 دولة")).ok
    assert check("أكثر من أربعة آلاف وسبعمية رحلة", heard("أكثر من 4700 رحلة")).ok
    assert check("وأكثر من عشرة آلاف تأخرت", heard("وأكثر من 10 ألاف تأخرت")).ok
    assert check("سبتمبر ألفين وواحد، وبألفين وثلاثة وعشرين", heard("سبتمبر 2001 وبـ 2023")).ok
    assert check("أوتلوك مال مايكروسوفت وقف", heard("Outlook مع المايكروسوفت وقف")).ok
    assert not check("أكثر من سبعين دولة", heard("أكثر من 17 دولة")).ok
    assert not check("شبكة الاتصالات عطلت", heard("شبكة عطلت")).ok


def test_arabic_brand_split_by_whisper():
    assert check("وتيمز وكوبايلوت هم تعطلوا", heard("وتيمز وكو بايلوت هما تعطلوا")).ok


def test_arabic_bare_clitics_ignored():
    assert check("ومايكروسوفت قالت: ماكو أي دليل", heard("و مايكروسوفت قالت ماكو أي دليل")).ok


def test_arabic_brand_close_spelling():
    assert check("وتعطلت أيضاً خدمتا تيمز وكوبايلوت.", heard("وتعطلت أيضا خدمتا تمز وكوبايلوت")).ok
    assert not check("وتعطلت أيضاً خدمتا تيمز وكوبايلوت.", heard("وتعطلت أيضا خدمتا وكوبايلوت")).ok
