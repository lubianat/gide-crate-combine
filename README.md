# Deliverable D10.1 – Model description and snapshot

> [!NOTE]
> This is an draft version for the [foundingGIDE](https://founding-gide.eurobioimaging.eu/about-us/) deliverable 10.1
> It will be formatted in a standard document for delivery


## Executive Summary


This report describes the data model and harmonized data for the foundingGIDE project, combining the study-level metadata of the major bioimaging data resources – BioImage Archive (BIA), Image DataResource (IDR), and SSBD:database. The Research Object Crate (RO-Crate) framework was chosen as the common base for the joint data model, for its large tooling ecosystem and compatibility with state-of-the-art semantic web standards. Building upon "Deliverable D6.1: Report in metadata model overlap and gaps", we developed a specification of a detached RO-Crate 1.2 profile describing an extensible framework, balancing expressiveness and ease of use. Data from the 3 repositories was converted into the common format by custom pipelines and validated programatically; the process was repeated iteratively, improving both the quality of original metadata sources as well as of this deliverable.

As an archive and reuse strategy, we provide the metadata for 1577 entries as individual JSON-LD documents, as well as a joint output serialized as RDF (Turtle format). We also provide a SHACL file specifying the data model in a machine-readable format, enabling validation pipelines. Finally, we leverage the ontology annotations of the datasets to enrich the deliverable with terms from the NCBI Taxonomy ontology and the Biological Imaging Methods Ontology (FBbi), improving the structure for comple searches.

Adittionally, we deliver a set of example SPARQL queries, as well as the source code for a visual query builder interface, demonstrating one way to explore the deliverable.

### Introduction

> [!NOTE]
> Bullet points present ideas to be expanded in 1-2 paragraphs

* A global data-sharing framework for bioimaging data — foundingGIDE vision and where the data deliverable fits

* Metadata harmonization and joint data model - agreement between different stakeholders on formats, standards, ontologies

* ... this report outlines the rationale for the choices in the data model


### Consensus building

* Technical ground work on comparing the models done by ["Deliverable D6.1: Report in metadata model overlap and gaps"](https://zenodo.org/records/16794787)

* Effective communication channels — monthly Biostream meetings with people in East Asia, South America and Europe, asynchronous communication via Slack and management via GitHub

* Choice of an external lightweight framework (RO-Crate) for all the partners to conform to, as well as relying upon existing metadata annotations

### Technical structure

* The RO-Crate 1.2 standard was chosen as a base, as it is flexible and extensible, and yet widely use. (explain RO-Crates)

* The standard builds upon JSON-LD to specify metadata for research objects, with simple constraints for interoperability. A living community, with tools and discussion forums. Improved FAIRness in comparison to developing a data model from scratch.

* The detached RO-Crate specification enabled decoupling of the metadata specification (lightweight JSON files) from the data-intensive payloads in the bioimagin repositories.

* The use of JSON-LD allowed the reuse of joint metadata both by applications that are familiar with JSON, as well as serialization as RDF triples for semantic web tooling.

#### The foundingGIDE RO-Crate profile

* The metadata model was built upon the https://schema.org framework, leveraging terms from Bioschemas and Darwin Core where needed.

* A direct benefit of building upon schema.org is the large semantic overlap with other specifications for standardized metadata for research datasets, including Google Dataset Search (https://developers.google.com/search/docs/appearance/structured-data/dataset?)
and CroissantML (https://mlcommons.org/working-groups/data/croissant/)


* (adapt the gide figure from www.figma.com/design/tP7Re2iAr2zTRuoTI5CWx6/SWAT-Poster-Amsterdam-2026?node-id=0-1&p=f&t=prKLkapf6BbQOvye-0)


* Minimally, each foundingGIDE dataset is linked to describing concepts through the schema.org "about" and "measurementMethod" properties. The values for these properties should be terms from the NCBI Taxonomy and FBbi ontology, respectively. This ensures a basic layer of semantic search.

* Additionally, datasets are enriched with descriptive properties, covering labels, descriptions and more




# Extra


Combining RO-Crates from SSBD, IDR and BIA into a single dump.


BIA crates: https://github.com/BioImage-Archive/gide-ro-crate/tree/main/study_ro_crates

IDR crates: https://github.com/German-BioImaging/idr_study_crates/tree/main/ro-crates

SSBD: https://github.com/openssbd/gide-ro-crate

## Approach


### 1 - Collect and Validate

* Get the *-ro-crate-metadata.json files, currently via git submodules (see 'collect_crates.py')

* Validate the RO-Crates using a SHACL profile (see "gide_shapes.ttl" for the profile and "validate_crates.py" for processing)

* Generate an "index.html" displaying the compliance stats for the crates

TODOS: Add URI-level validation (are ontology entries correct/valid? which ontologies are used?)

### 2 - Serialize and export

TODOS:

* Serialize in RDF

Something like https://github.com/German-BioImaging/idr_study_crates/blob/main/scripts/batch_generate.py#L3102

```python
def write_merged_ttl(
    output_path: Path, output_dir: Path, subcrates, index_path: Optional[Path]
) -> None:
    try:
        from rdflib import Graph
    except ImportError as exc:
        raise SystemExit(
            "rdflib is required to write Turtle output. Run with `uv run` or install via `python3 -m pip install rdflib`."
        ) from exc

    from rdflib.plugins.shared.jsonld import context as jsonld_context

    graph = Graph()
    original_fetch = jsonld_context.Context._fetch_context

    def _fetch_context(self, source: str, base: Optional[str], referenced_contexts):  # type: ignore[no-untyped-def]
        source_url = urljoin(base or "", source)
        if source_url == RO_CRATE_CONTEXT_URL:
            return RO_CRATE_CONTEXT_FALLBACK
        return original_fetch(self, source, base, referenced_contexts)

    jsonld_context.Context._fetch_context = _fetch_context
    try:
        if index_path is not None:
            index_data = json.loads(index_path.read_text(encoding="utf-8"))
            index_base = crate_base_iri(index_data, index_path.resolve().as_uri())
            graph.parse(
                data=json.dumps(index_data), format="json-ld", publicID=index_base
            )

        for descriptor_file, crate in subcrates:
            crate_path = (output_dir / descriptor_file).resolve()
            crate_base = crate_base_iri(crate, crate_path.as_uri())
            graph.parse(data=json.dumps(crate), format="json-ld", publicID=crate_base)
    finally:
        jsonld_context.Context._fetch_context = original_fetch

    output_path.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=str(output_path), format="turtle")
```

* Output to Zenodo or something as a GIDE deliverable


### 3 - Enrich and query

* Enrich with upper terms from FBbi and NCBITaxon (see https://github.com/German-BioImaging/idr_study_crates/blob/main/scripts/join_with_fbbi_and_ncbitaxon.py)


* Upload as a dataset to Triply (via API, ideally)

* Set up a fork of https://github.com/German-BioImaging/idr-sparnatural pointing to the joint endpoint.
