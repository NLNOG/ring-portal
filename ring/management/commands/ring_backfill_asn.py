"""Backfill participant ASNs (ParticipantProfile) from Machine.autnum.

Participants historically have no ASN column; the ASN lives on machines.
Now that the app stores participant ASNs in the SQLite-side ParticipantProfile
(never in the read-only legacy database), this command populates that profile:
single distinct ASN wins, otherwise the most common is used and the
participant is reported so an admin can review.
"""

from django.core.management.base import BaseCommand
from django.db.models import Count

from ring.models import Machine, Participant, ParticipantProfile
from ring.services.profiles import participant_autnum, set_participant_autnum


class Command(BaseCommand):
    help = "Backfill participant ASNs in ParticipantProfile from machines.autnum"

    def handle(self, *args, **options):
        verbose = options.get("verbosity", 1) > 0
        by_participant = (
            Machine.objects.exclude(autnum__isnull=True)
            .values("owner__participant_id")
            .annotate(n=Count("autnum", distinct=True))
            .order_by()
        )
        rows = {
            p["owner__participant_id"]: p["n"]
            for p in by_participant
        }
        ambiguous = []
        conflicts = []
        updated = 0
        for participant in Participant.objects.order_by("pk"):
            if participant_autnum(participant.pk) is not None:
                continue
            asns = list(
                Machine.objects.filter(owner__participant=participant)
                .exclude(autnum__isnull=True)
                .values_list("autnum", flat=True)
            )
            if not asns:
                continue
            counts = {}
            for asn in asns:
                counts[asn] = counts.get(asn, 0) + 1
            distinct = rows.get(participant.pk, 0)
            if distinct > 1:
                ambiguous.append(participant)
                asn = max(counts, key=counts.get)
            else:
                asn = asns[0]
            claimed = (
                ParticipantProfile.objects.filter(autnum=asn)
                .exclude(participant_id=participant.pk)
                .first()
            )
            if claimed is not None:
                conflicts.append((participant, asn, claimed.participant_id))
                continue
            set_participant_autnum(participant.pk, asn)
            updated += 1
            if verbose:
                self.stdout.write(
                    "%-5s participant %s <- AS%s" % (participant.pk, participant.company, asn)
                )
        if verbose:
            self.stdout.write(self.style.SUCCESS("Backfilled %d participant(s)." % updated))
        if ambiguous:
            still = []
            for participant in ambiguous:
                distinct_asns = (
                    Machine.objects.filter(owner__participant=participant)
                    .exclude(autnum__isnull=True)
                    .values_list("autnum", flat=True)
                    .distinct()
                )
                still_count = sum(1 for a in distinct_asns if a != participant_autnum(participant.pk))
                if still_count:
                    still.append(participant)
            if still:
                self.stdout.write(
                    self.style.WARNING(
                        "Participants with machines on multiple ASNs to review:"
                    )
                )
                for participant in still:
                    self.stdout.write(
                        "  participant %s (AS%s)" % (participant.company, participant_autnum(participant.pk))
                    )
        if conflicts:
            self.stdout.write(
                self.style.WARNING(
                    "ASNs already assigned to another participant (manual review):"
                )
            )
            for participant, asn, owner_id in conflicts:
                owner = Participant.objects.filter(pk=owner_id).first()
                self.stdout.write(
                    "  participant %s (pk=%s) <- AS%s (already %s, pk=%s)"
                    % (
                        participant.company,
                        participant.pk,
                        asn,
                        owner.company if owner else "participant",
                        owner_id,
                    )
                )