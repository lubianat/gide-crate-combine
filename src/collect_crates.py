import json
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

HERE = Path(__file__).parent.resolve()
BIA_CRATES_FOLDER = HERE / "gide-ro-crate" / "study_ro_crates"
IDR_CRATES_FOLDER = HERE / "idr_study_crates" / "ro-crates"
SSBD_DB_FOLDER = HERE / "gide-ro-crate-openssbd" / "project-ro-crate" / "database"
SSBD_REPO_FOLDER = HERE / "gide-ro-crate-openssbd" / "project-ro-crate" / "repository"
destination_folder = HERE / "../data_deliverable/GIDE_crates"
destination_folder.mkdir(exist_ok=True)


def clean_context(context):
    """Remove datePublished type coercion from @context, handling various shapes.

    e.g.
    @context: {
    "https:/",
    { "something": "http://example.com/something",
      "datePublished": {
        "@id": "http://schema.org/datePublished",
        "@type": "xsd:date"
      }

    becomes

    @context: {
    "https:/",
    { "something": "http://example.com/something"
      }

    also remove       "Taxon": {
        "@id": "dwc:Taxon"
      },

    if it was set
    """
    for item in context:
        if isinstance(item, dict):
            # Remove datePublished if it has @type xsd:date
            if "datePublished" in item:
                dp = item["datePublished"]
                if isinstance(dp, dict) and dp.get("@type") == "xsd:date":
                    del item["datePublished"]
            if "Taxon" in item:
                tax = item["Taxon"]
                if isinstance(tax, dict) and tax.get("@id") == "dwc:Taxon":
                    del item["Taxon"]
    return context


def clean_crate(file: Path, dest: Path):
    """Parse, clean, and write a single RO-Crate metadata file."""
    data = json.loads(file.read_text(encoding="utf-8"))

    if "@context" in data:
        data["@context"] = clean_context(data["@context"])

    # Fix; Change any @ids of the kind "*-ro-crate-metadata.json" to "ro-crate-metadata.json"
    # see note in https://www.researchobject.org/ro-crate/specification/1.2/root-data-entity.html
    #     { "@context": "https://w3id.org/ro/crate/1.2/context",
    #   "@graph": [
    #     {
    #         "@type": "CreativeWork",
    #         "@id": "ro-crate-metadata.json",
    #         "about": {"@id": "./"},
    #         "conformsTo": {"@id": "https://w3id.org/ro/crate/1.2"}
    #     },

    #     {
    #       "@id": "./",
    #       "@type": "Dataset",
    #       ...
    #     }
    #   ]
    # }
    if "@graph" in data:
        for entity in data["@graph"]:
            if (
                isinstance(entity, dict)
                and ("CreativeWork" in entity.get("@type", []))
                and entity.get("@id", "").endswith("-ro-crate-metadata.json")
            ):
                entity["@id"] = "ro-crate-metadata.json"
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


folders = {
    "BIA_CRATES_FOLDER": BIA_CRATES_FOLDER,
    "IDR_CRATES_FOLDER": IDR_CRATES_FOLDER,
    "SSBD_DB_FOLDER": SSBD_DB_FOLDER,
    "SSBD_REPO_FOLDER": SSBD_REPO_FOLDER,
}

for folder_name, folder in folders.items():
    if not folder.exists():
        logger.warning(f"Folder does not exist: {folder_name} ({folder})")
        continue

    files = list(folder.glob("*-ro-crate-metadata.json"))
    if files:
        logger.info(f"Found {len(files)} RO-Crate metadata file(s) in: {folder_name}")
        for file in files:
            try:
                clean_crate(file, destination_folder / file.name)
                logger.debug(f"Cleaned and copied: {file.name}")
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in {file.name}: {e}")
            except Exception as e:
                logger.error(f"Failed to process {file.name}: {e}")
    else:
        logger.debug(f"No RO-Crate metadata files found in: {folder_name}")
