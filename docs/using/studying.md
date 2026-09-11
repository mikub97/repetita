# Studying

The Study tab asks one question at a time and decides which one. There is
nothing to configure to begin — open it and answer.

```
  ▪▪▫▪▪▪▫▪▪▫▫        Na mesa faltam os ___: garfo, faca e colher.
  ▫▫▫                sztućce (wyraz zbiorczy)
  12 of 29           Na stole brakuje sztućców: widelca, noża i łyżki.
  12 new
                     [                              ]  Check
                     I know this     Something's wrong
```

**The rail on the left is the session.** One mark per card, filled as you go, so
you can see how much is left without a progress bar pretending to be a
percentage. `12 of 29` is where you are; `12 new` is how much of it is material
you have never seen.

**The card is the question and only the question.** Whatever the course declares
as visible before answering — the cue, a hint, the translation — is on screen.
The answer is not in the page at all until you have answered: not hidden with
CSS, not in a data attribute. It is not sent to the browser.

## Answering

Type it and press **Check** (or Enter). The grading happens on the server, and
the verdict comes back with the answer and a character-level diff. Being told
only *wrong* after typing ten words is a punishment rather than feedback, so you
are always shown what you typed against what was expected.

Accents count. `esta` and `está` are different words, and an accent-only miss is
treated gently rather than being ignored.

Some exercises are not typed at all: choose from four, say it aloud and judge
yourself, rearrange a sentence. Which form an exercise takes is the course's
decision, and one piece of material can legitimately be asked in several of them.

## The two buttons under the box

**I know this** takes a card out of the queue without pretending you studied it.
Use it for material you already know — it is recorded as *marked*, never as
*answered*, so your statistics keep telling the truth. It is reversible.

**Something's wrong** is for when the exercise itself is the problem: the answer
is wrong, the hint gives it away, the Polish is off. It files an issue against
that material, which you or an agent can act on later from the
[Manage](managing.md) tab. Nothing about your schedule changes.

## What "owed" and "today" mean

The header carries two numbers.

**Owed** is everything due now — the backlog. It does not reset at midnight and
it does not shame you: a card that has waited three weeks is simply due.

**42 / 30 today** is answers given today against the day's target. New material
is introduced against that target, so a day where you clear a backlog does not
also bury you in new words.

## When the queue is empty

You are done for the day. New material is introduced at a rate the plan decides
(*one new card after every three you already owe*, by default), rather than all
at once, because the fastest way to make tomorrow unbearable is to start forty
new things today.

If you want more anyway, the [Design tab](designing.md) is where you say what
that *more* should be about.
