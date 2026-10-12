"""The estimate as a document: the same template serves the PDF and the customer's
/d/ page."""

from app.estimates import service
from app.extensions import db
from app.models import APPROVAL_METHODS, Site
from app.work import files
from app.work.docs import render_pdf


def context(est, customer):
    jobs, lines = service.jobs_and_lines(est)
    terms = est.terms_snapshot if est.terms_snapshot is not None else \
        service.shop().estimate_terms
    part_links = service.part_links(est)
    return dict(est=est, customer=customer, jobs=jobs, lines=lines, label=service.label(est),
                totals=service.totals([x for ls in lines.values() for x in ls]),
                part_links=part_links,
                buy_totals=service.buy_totals([r for rs in part_links.values() for r in rs]),
                line_amount=service.line_amount, methods=APPROVAL_METHODS, terms=terms,
                site=db.session.get(Site, est.site_id) if est.site_id else None)


def pdf(est, customer):
    sig = files.path_for(est.signature_sha256, ".png").as_uri() if est.signature_sha256 else None
    return render_pdf("docs/estimate.html", signature_src=sig, **context(est, customer))


def filename(est):
    return f"{service.label(est).replace(' ', '-')}.pdf"
