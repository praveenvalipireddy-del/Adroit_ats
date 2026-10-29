"""Test helper (not a test): walk the Optimize window's steps in a Playwright page.

1 Match check -> 2 Choose changes -> Apply -> 3 Review resume -> (4 Email). The suggested changes are ticked
except the "not in the resume" ones, the same skills the one-step optimizer used to add.
"""

READY = ("document.querySelector('#do-step1 .mc-pct') && document.getElementById('btn-do-next').offsetParent !== null"
         " && !document.getElementById('btn-do-next').disabled")


def through_apply(page, timeout=20000):
    """From an open Optimize window that is checking the match: to step 3 with the changes made."""
    page.wait_for_function(READY, timeout=timeout)
    page.click("#btn-do-next")                       # -> 2 Choose changes
    page.evaluate("document.querySelectorAll('#do-step2 label.ch-item:not(.missing) input').forEach(i => {"
                  " i.checked = true; i.dispatchEvent(new Event('change')); })")
    page.click("#btn-do-next")                       # Apply
    page.wait_for_function("optPanel.step === 3", timeout=timeout)


def to_email(page, timeout=15000):
    """Step 3 -> 4 (the email), once it has been written."""
    page.wait_for_function("optPanel.step >= 3", timeout=timeout)
    if page.evaluate("optPanel.step") == 3:
        page.click("#btn-do-next")
    page.wait_for_function("optPanel.step === 4", timeout=timeout)
