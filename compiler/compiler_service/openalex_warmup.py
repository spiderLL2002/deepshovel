"""Download and initialize the OpenAlex-official topic model."""

from __future__ import annotations

from .config import Settings
from .openalex_topics import OpenAlexTopicClassifier


def main() -> None:
    settings = Settings.from_environment()
    classifier = OpenAlexTopicClassifier(
        api_key=settings.openalex_api_key,
        cache_dir=settings.data_dir / "openalex" / "models",
    )
    classifier.warmup()
    print(f"OpenAlex topic model ready: {classifier.model_name}")


if __name__ == "__main__":
    main()
