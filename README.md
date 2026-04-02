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

PLACEHOLDER IMAGE
![alt text](temp_image.png)

* Minimally, each foundingGIDE dataset is linked to describing concepts through the schema.org "about" and "measurementMethod" properties. The values for these properties should be terms from the NCBI Taxonomy and FBbi ontology, respectively. This ensures a basic layer of semantic search.

* Additionally, datasets are enriched with descriptive properties, includding support for labels, descriptions, thumbnail URLs, funder information and similar

* Notably, the profile only covers contextual metadata about the whole studies, and does not provide direct means of programatically downloading individual files. Metadata for file-level access will be ultimately useful when formats and data access protocols are standardized, for example with the wide adoption of OME-Zarr by imaging repositories.

* The study-level standard profile can also contribute to integrate general research data repositories (such as Dryad or figshare) into a common bioimage index


### The data integration process

* Each of the 3 bioimage data repositories considered in this project (SSBD, IDR and BIA) developed custom pipelines for metadata processing, outputing RO-Crate metadata in GitHub repositories:
  * https://github.com/BioImage-Archive/gide-ro-crate.
  * https://github.com/German-BioImaging/idr_study_crates
  * https://github.com/openssbd/gide-ro-crate

* Metadata was harvested from these repositories and validated through a series of steps, improving the quality of the output.

* RO-Crates were validated against a SHACL, machine readable version of the GIDE RO-Crate profile (data_deliverable/gide_profile_shacl_shape.ttl)

* The output of the validation was parsed into an HTML web page, allowing for visual exploration of inconsistencies (data_deliverable/gide_profile_validation.html)

* Furthermore, a custom validator for the ontology terms was made, accessing the consistency of identifiers and labels in the RO-Crates against the values in the ontology source code. This validator also outputted an HTML web page for visual exploration (data_deliverable/gide_ncbi_and_fbbi_validation.html)

* The content of the RO-Crates were parsed in Python using the rdflib package, combining the metadata in a single RDF graph, serialized in the Turtle format. The pipeline ensured both that (1) the RO-Crates were well formed not only as JSON, but also as true Linked Open Data and (2) that a single file containing triples was available, making it easy to develop applications on top of it, such as loading in triplestores and running SPARQL endpoints. (data_deliverable/gide_metadata_combined.ttl)

* To make it easier to leverage the use of ontologies, the combined output was enriched with parent terms from FBbi ontology and NCBI taxonomy. This enables deterministic searches using the hierarchies. For example, a search for "studies about bacteria that used light microscopy" will include all taxa that fall under the "Bacteria" category on NCBI and all imaging modalities under "light microscopy" on FBbi. (data_deliverable/gide_metadata_with_ontologies.ttl)
