from repetita import graders
from repetita.core.protocols import GradingOptions
from repetita.core.types import Rating, Response

PT = GradingOptions(fold_accents=True, sentence_slack=1)


def typed(text, accepted, opts=PT):
    return graders.get("typed").grade(Response(text=text), accepted, opts=opts)


def sentence(text, accepted, opts=PT):
    return graders.get("sentence").grade(Response(text=text), accepted, opts=opts)


class TestTyped:
    def test_exact_match_is_good(self):
        assert typed("hoje", ["hoje"]).rating is Rating.GOOD

    def test_case_and_spacing_are_forgiven(self):
        assert typed("  HOJE ", ["hoje"]).rating is Rating.GOOD

    def test_missing_accent_is_hard_not_a_miss(self):
        assert typed("amanha", ["amanhã"]).rating is Rating.HARD
        assert typed("sabado", ["sábado"]).rating is Rating.HARD

    def test_a_different_word_is_a_miss(self):
        assert typed("ontem", ["amanhã"]).rating is Rating.AGAIN

    def test_empty_is_a_miss(self):
        assert typed("   ", ["hoje"]).rating is Rating.AGAIN

    def test_any_accepted_answer_counts(self):
        assert typed("carro", ["automóvel", "carro"]).rating is Rating.GOOD

    def test_accent_folding_is_a_course_setting(self):
        strict = GradingOptions(fold_accents=False)
        assert typed("amanha", ["amanhã"], strict).rating is Rating.AGAIN


class TestSentence:
    def test_exact_sentence_is_good(self):
        j = sentence("Eu saio de casa às oito", ["Eu saio de casa às oito"])
        assert j.rating is Rating.GOOD

    def test_final_punctuation_does_not_matter(self):
        j = sentence("Eu saio de casa.", ["Eu saio de casa"])
        assert j.rating is Rating.GOOD

    def test_one_wrong_word_in_a_long_sentence_is_hard(self):
        j = sentence("Eu saio de casa as nove", ["Eu saio de casa as oito"])
        assert j.rating is Rating.HARD

    def test_two_wrong_words_is_a_miss(self):
        j = sentence("Eu entro na casa as nove", ["Eu saio de casa as oito"])
        assert j.rating is Rating.AGAIN

    def test_scores_against_the_closest_accepted_phrasing(self):
        answers = [
            "Eu comprei verduras na feira de manhã",
            "De manhã eu comprei verduras na feira",
        ]
        j = sentence("De manhã eu comprei verduras na feira", answers)
        assert j.rating is Rating.GOOD
        assert j.matched == answers[1]

    def test_diff_says_which_word_was_wrong(self):
        j = sentence("Eu saio de casa as nove", ["Eu saio de casa as oito"])
        wrong = [t for t in j.diff if t.kind == "wrong"]
        assert [(t.given, t.expected) for t in wrong] == [("nove", "oito")]

    def test_hyphenated_words_stay_whole(self):
        j = sentence("Vou na segunda-feira", ["Vou na segunda-feira"])
        assert j.rating is Rating.GOOD
        assert len(j.diff) == 3

    def test_empty_is_a_miss_but_still_shows_the_answer(self):
        j = sentence("", ["Eu saio de casa"])
        assert j.rating is Rating.AGAIN
        assert j.matched == "Eu saio de casa"


class TestChoice:
    def test_no_partial_credit_for_a_selection(self):
        g = graders.get("choice")
        assert g.grade(Response(choice="o"), ["o"], opts=PT).rating is Rating.GOOD
        assert g.grade(Response(choice="a"), ["o"], opts=PT).rating is Rating.AGAIN


class TestSelf:
    def test_learner_supplies_the_rating(self):
        g = graders.get("self")
        assert g.grade(Response(choice="3"), ["x"], opts=PT).rating is Rating.GOOD
        assert g.grade(Response(choice="1"), ["x"], opts=PT).rating is Rating.AGAIN

    def test_nonsense_is_treated_as_a_miss(self):
        g = graders.get("self")
        assert g.grade(Response(choice="banana"), ["x"], opts=PT).rating is Rating.AGAIN
