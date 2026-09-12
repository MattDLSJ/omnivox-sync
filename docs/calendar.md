# How to build the calendar

Instructions for an AI agent with a calendar connector. If you do not have one,
stop reading: run `make ics` and hand over the file it writes.

This is not a style guide. Every rule below exists because the obvious version
was tried and something went wrong with it.

## Match what `make ics` already produces

The project can already generate this calendar as a file, and that file is the
reference implementation of everything below. Before inventing a format, run:

    make ics

and read one event out of the `.ics` it writes. If what you create through a
connector does not look like that, the two halves of this project disagree and
the person ends up with two different-looking calendars depending on which path
they took.

## One event per meeting, never a weekly recurrence

A cégep term is not a clean weekly repeat and a recurrence rule gets it wrong
silently. Two reasons, both real:

- **Day swaps.** The college calendar marks some days `/Lu`, `/Ma` and so on,
  meaning that date runs *another weekday's* timetable. A Tuesday that runs
  Monday's classes is a Tuesday your recurrence fills with the wrong courses.
- **Cancellations.** `X` congé férié, `JP` journée pédagogique, `JM` mise à
  jour, `JE` encadrement, `JR` journée particulière. None of them run regular
  classes, and a course that meets only on the cancelled weekday loses that
  week entirely, so per-course meeting counts genuinely differ.

So: one `create_event` per actual meeting, roughly 110 a semester. Batch them.
It takes a few minutes and it is the expected path.

**Consecutive periods are ONE event.** A course scheduled 08:10 to 12:00 is a
single four-period event, not four fifty-minute ones. Breaks inside it are the
teacher's call, not the calendar's.

## Before you create anything

- **Say which account and which calendar you are writing to**, by name. A
  calendar written into the wrong account is invisible and confusing to find,
  and nobody notices for a week.
- **List the existing events over the semester first.** Most connectors cannot
  set a stable event id, so a second run duplicates rather than updates. If
  class events are already there, do not re-run the creation: find the one
  event that is wrong and update or delete that.

## The shape of a class event

Read it out of `config.yaml`: `courses:` has the code, folder name, icon,
teacher and group, and `schedule:` has the weekday, hours, room and block
letter. Both are filled in automatically by `make discover`.

**Title:** `{icon} {class number} · {course folder name}`

    🏛️ 7 · Histoire du monde

The icon is the course's `icon:` field, the same emoji as its folder, so the
calendar and the file system read as one thing. The number is which meeting of
the term this is, counting from one.

**Location:** the classroom, then the teacher.

    Local Z016 · Bruno Lacasse

Not the college's street address. The location is what shows inside the
ten-minute reminder, and at that moment the useful fact is which room to walk
to, not which city the cégep is in.

**Description:** five identifying lines, then three empty working headings.

    <b>Histoire du monde</b><br>202-201-EM gr.1100<br>Local Z016    T<br>
    Bruno Lacasse<br><b><i>Présentiel</i></b>
    <p><strong>7 À faire / Devoirs :</strong></p>
    <p><strong>Contenu du cours :</strong></p>
    <p><strong>Notes / Rappels :</strong></p>

Four spaces before the block letter. The three headings ship **empty**: the
first two get filled in as the term goes, and `Notes / Rappels` is the
student's own scratch space, so never write into it.

Line breaks must be `<br>`. Google renders a small subset of HTML in this
field, and inside HTML a bare newline is whitespace: it collapses and the five
lines run together into one paragraph.

The letter after the room is the Omnivox activity type: **T** théorie, **L**
labo, **E** encadrement. Keep it. When the same course meets twice a week it is
often the only thing distinguishing the two sessions, and the room usually
differs between them as well, so read the room per schedule entry rather than
per course.

**Reminder:** one popup, 10 minutes before.

## Evaluations

These come from the plans de cours in the course folders, not from Omnivox.

Three rules, and all three matter:

- **Colour.** Grape. That is `colorId: "3"` in the Google Calendar API.
  Ordinary class events carry no colour and inherit the calendar's default, so
  an exam is the only thing that stands out in month view.
- **The weighting is never omitted.** Put the percentage in the description
  **and in the title**, so it is readable in month view without opening
  anything. An evaluation whose weight you cannot see is one you cannot
  prioritise.
- **Placement.** The evaluation is the **first bullet under "À faire /
  Devoirs", in bold**, not a separate paragraph above the headings:

      <p><strong>6 À faire / Devoirs :</strong></p>
      <ul><li><strong>📝 Mini-test 1 en classe (15 %)</strong></li>
          <li>Documents sur LÉA</li></ul>

  Bullets are `<ul><li><p>text</p></li></ul>`.

**Title for an evaluation:**

    {icon} {number} · {course} · 📝 {evaluation} ({x} %)

## The college's own year calendar

Add it. It is the part everybody forgets until the week it matters: reading
week, the exam period, the days with no classes.

Read the college's PDF **as a rendered image**, with something like
`pdftoppm -png`. Do not use `pdftotext`: the calendar is a grid, and extracting
it as text scrambles which code belongs to which date, which is exactly the
information you came for.

Codes that mean no regular classes: `X`, `JP`, `JM`, `JE`, `JR`, `JR*`.
Codes about exams: `EHR` is a terminal exam **at the regular class time**, so
it replaces that class rather than adding to it; `EC` is a common exam
scheduled separately; `EUL` is the épreuve uniforme de langue.

## Check your work against the timetable

The horaire lists each course's weekly contact hours as Th + Lab. Add up the
hours of the events you created for one course in one week; they should match.
This is worth doing rather than skipping: it is how an activity block that is
not a weekly class gets identified as one, which would otherwise put a
recurring event in the calendar for something that meets once.
