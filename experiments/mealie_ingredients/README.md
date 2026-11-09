# Mealie ingredient parsing experiment

This is a standalone experiment, not a Home Assistant integration change. The 40
synthetic ingredient lines (20 Swedish, 20 English) are a starter dataset, not a
representative sample of your recipes. Review the labels and replace or extend
the examples with real recipe lines before choosing a parser.

Home Assistant's `mock_mealie_client` returns fixtures and does not execute an
ingredient parser. It cannot measure parsing accuracy.

## Environment

Home Assistant's required `script/setup` has been run separately. This experiment
uses its own Python 3.14 environment to avoid adding parser dependencies to HA.
From the repository root, recreate it with:

```bash
bash experiments/mealie_ingredients/setup
```

Setup needs network access to install the pinned packages in `requirements.lock`
and download NLTK's English tagging model into `.venv/nltk_data`. `requirements.txt`
records the two direct dependencies; the lock file pins their installed dependency
versions. The external tagging-model download is not version pinned.

## Run now, without a Mealie server

```bash
experiments/mealie_ingredients/.venv/bin/python3 experiments/mealie_ingredients/run.py \
  --output experiments/mealie_ingredients/results/local-01
```

The default backend executes `ingredient-parser-nlp==2.9.1`, the dependency pinned
by the Mealie source inspected for this experiment. This is the underlying NLP
library, not Mealie's full parser: it omits Mealie's amount processing and matching
against a household's existing foods and units.

The library supports English only. Both language groups intentionally use the
English model; Swedish results measure unsupported-language behavior, not a
Swedish parser. See the [library API documentation](https://ingredient-parser.readthedocs.io/en/stable/reference/parsers.html)
and [Mealie parser implementation](https://github.com/mealie-recipes/mealie/blob/mealie-next/mealie/services/parser_services/ingredient_parser.py).

The initial 30-ingredient run is saved in `results/local-baseline/`. It predates
the additional English cases en11–en20:

| Group | Food exact/alias matches | Quantity matches | Unit matches |
| --- | --- | --- | --- |
| Swedish | 7/20 | 18/19 | 12/20 |
| English | 9/10 | 10/10 | 10/10 |

These are field comparisons on a small authored dataset, not product-matching
accuracy. In particular, qualifiers may be moved into preparation/comment fields,
so review the raw output before interpreting a food-name mismatch.

## Run against Mealie later

Create an API token in Mealie and enter it locally without putting it in a file
or shell history:

```bash
read -rsp 'Mealie API token: ' MEALIE_TOKEN
export MEALIE_TOKEN
experiments/mealie_ingredients/.venv/bin/python3 experiments/mealie_ingredients/run.py \
  --backend mealie --parser nlp \
  --url http://localhost:9000 --mealie-version YOUR_VERSION \
  --output experiments/mealie_ingredients/results/mealie-nlp-01
unset MEALIE_TOKEN
```

Use your actual base URL and version. The runner calls
`POST /api/parser/ingredient` once per line and saves Mealie's response. It does
not save edits to recipes. Use `--parser brute` with a different output directory
to compare the other non-AI parser. Verify the endpoint in your instance's `/docs`
if your version differs. The [upstream route](https://github.com/mealie-recipes/mealie/blob/mealie-next/mealie/routes/parser/ingredient_parser.py)
and [request schema](https://github.com/mealie-recipes/mealie/blob/mealie-next/mealie/schema/recipe/recipe_ingredient.py)
were checked when preparing the runner. AI parsing is deliberately outside this
experiment.

## Read and extend the results

Each run requires a new output directory and produces:

- `responses.json`: metadata, a frozen copy of every input/expected label, and raw
  parser responses, including confidence information where available.
- `review.csv`: expected/actual food, quantity and unit, comparison flags, and a
  manual qualifier-preservation column. Review qualifiers across all raw fields.
- `summary.json`: separate Swedish/English counts. Request errors count as failed
  comparisons and produce a nonzero exit code.

Food and unit scoring uses case/whitespace normalization and explicit `|`-separated
accepted names. It does not translate, fuzzy-match, or convert units. An empty
expected unit requires no extracted unit. Unspecified quantities are excluded
from the quantity denominator. For package examples, only the outer quantity/unit
is automatically compared; inspect nested package weights in the raw responses.
The local backend compares the first extracted amount and retains every amount
in the raw output. It joins multiple food names rather than silently selecting one.

`qualifiers` describes constraints such as lactose-free, gluten-free, or frozen;
their correctness is manually reviewed separately from food-name extraction.
`notes` explains label choices. To check an edited dataset without parsing:

```bash
experiments/mealie_ingredients/.venv/bin/python3 experiments/mealie_ingredients/run.py --validate
```

## Matpriskollen product worksheet

`products.csv` has one blank row per ingredient. No real offers have been fetched
or invented: the public Matpriskollen page timed out during setup. Copy exact
product titles into `product_name`, with brand, package size, store, source URL,
and observation date where available. Add rows for multiple candidate products.
Label `expected_match` as `yes`, `no`, or `uncertain`, and explain the reason.
Include negative examples such as ordinary cream for a lactose-free requirement.

The 20 populated rows contain user-supplied offers from Coop
Mariagatan, preserved in `fixtures/matpriskollen_coop_mariagatan.json`.
For those rows, `product_name` is exactly the API's `product.name`; `brand` and
`package_size` retain the separate brand and volume. `offer_id` identifies the
source offer. Soap and amaryllis are retained as explicitly marked non-food
examples. Recipe associations are left empty unless an existing ingredient fits.
Expected food names are manually authored benchmark labels, not API annotations.

The synthetic product examples have been removed; older result directories still
record those earlier inputs.

To send all 20 nonempty `product_name` fields to Mealie:

```bash
experiments/mealie_ingredients/.venv/bin/python3 experiments/mealie_ingredients/run.py \
  --backend mealie --parser nlp \
  --url http://mealie:9000 --mealie-version 3.28.0 \
  --products experiments/mealie_ingredients/products.csv \
  --output experiments/mealie_ingredients/results/products-mealie-03
```

Use the terminal where you exported `MEALIE_TOKEN`. Blank product titles are
skipped. Only the exact product title is sent to the parser, including the brand
and package size. Product mode also works with the default local backend.
`responses.json` retains the worksheet row and complete parser response;
`review.csv` shows extracted food, quantity, and unit, with manual columns for
brand removal, qualifier preservation, and package-size correctness. Product
mode compares against `expected_food_names`, `expected_quantity`, and
`expected_unit_names` in the worksheet. These labels describe the product's
package, not the recipe amount. Food and unit alternatives use `|`; food labels
exclude brands and retain qualifiers such as lactose-free and frozen. A qualifier
moved into a note does not satisfy this strict food-field comparison, so inspect
raw responses as well. No unit conversions are applied. The summary includes
correct/total counts and percentages for food, quantity, unit, and all three
fields together. Request errors count as incorrect for every field.
Blank expected quantities/units are excluded from those metrics and from the
all-fields metric; the imported API rows intentionally omit them because the
input name contains no package volume. The original `volume` is retained without
flattening ranges, multipacks, or approximate sizes. Each metric reports its own
denominator.

To score an existing product run without making any new requests:

```bash
experiments/mealie_ingredients/.venv/bin/python3 experiments/mealie_ingredients/run.py \
  --products experiments/mealie_ingredients/products.csv \
  --rescore experiments/mealie_ingredients/results/products-mealie-01/responses.json \
  --output experiments/mealie_ingredients/results/products-mealie-01-scored
```

Rescoring requires the same nonempty product titles and ingredient associations
in the same order. It copies the responses into the new output directory with
current expected labels, keeping the original run intact. It needs no API token.

The runner does not score product matching yet. The worksheet contains no prices,
so it also cannot measure recipe savings yet. Generated results and virtual
environments are ignored by Git; review all source files before sharing changes.
