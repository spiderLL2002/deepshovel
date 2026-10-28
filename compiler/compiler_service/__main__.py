"""Run the standalone compiler on a non-conflicting localhost port."""

import uvicorn


def main() -> None:
    uvicorn.run(
        "compiler_service.api:create_app",
        factory=True,
        host="127.0.0.1",
        port=8011,
    )


if __name__ == "__main__":
    main()
