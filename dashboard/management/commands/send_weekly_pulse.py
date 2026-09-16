from django.core.management.base import BaseCommand

from dashboard.services.weekly_pulse import run_weekly_pulse


class Command(BaseCommand):
    help = "Generate (and optionally push) the weekly Business Pulse for all active users."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-push", action="store_true",
            help="Only regenerate in-app digests; do not push to WhatsApp.",
        )
        parser.add_argument(
            "--limit", type=int, default=None,
            help="Process at most N users (for testing).",
        )

    def handle(self, *args, **options):
        result = run_weekly_pulse(push=not options["no_push"], limit=options.get("limit"))
        self.stdout.write(self.style.SUCCESS(
            f"Weekly pulse done: processed={result['processed']} "
            f"generated={result['generated']} pushed={result['pushed']}"
        ))
