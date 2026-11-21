"""python manage.py eval_agents [--suite routes|monitor|risk ...]

Prints the eval table (plan.md B.9) and exits non-zero if any case fails.
"""
from django.core.management.base import BaseCommand, CommandError

from autopilot.evals import SUITES, run_evals
from autopilot.runner import MODEL, gemini_available


class Command(BaseCommand):
    help = "Run agent evals: route invariants, monitor scenarios, risk classification."

    def add_arguments(self, parser):
        parser.add_argument("--suite", action="append", choices=list(SUITES), help="repeatable; default all")

    def handle(self, *args, suite=None, **options):
        rows = run_evals(suite)
        mark = {True: "PASS", False: "FAIL", None: "skip"}
        w_case = max(len(r.case) for r in rows)
        w_exp = max(len(r.expect) for r in rows)
        self.stdout.write(f"Monora agent evals · model {MODEL if gemini_available() else 'none (deterministic fallbacks)'}\n")
        self.stdout.write(f"{'suite':8} {'case':{w_case}} {'result':6} {'ms':>6}  {'expected':{w_exp}}  got")
        for r in rows:
            self.stdout.write(f"{r.suite:8} {r.case:{w_case}} {mark[r.passed]:6} {r.ms:>6}  {r.expect:{w_exp}}  {r.got}")
        passed = sum(r.passed is True for r in rows)
        failed = sum(r.passed is False for r in rows)
        skipped = sum(r.passed is None for r in rows)
        self.stdout.write(f"\n{passed} passed · {failed} failed · {skipped} skipped")
        if failed:
            raise CommandError(f"{failed} eval case(s) failed")
