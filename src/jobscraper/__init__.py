"""jobscraper: scrape job postings and extract structured requirements."""

from jobscraper.models import MatchResult, Posting
from jobscraper.pipeline import process_url
from jobscraper.scoring import score_posting

__version__ = "1.0.0"
__all__ = ["Posting", "MatchResult", "process_url", "score_posting"]
