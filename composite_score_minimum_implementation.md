# Minimum Implementation for Scramble-Calibrated `composite_score`

## Purpose

`composite_score` is currently an uncalibrated combination of ColabFold-derived confidence and interface metrics. Its raw value may be useful for ranking models generated under a fixed protocol, but it does not directly indicate whether a peptide scores better than an appropriate sequence-level null expectation.

The minimum defensible implementation is to evaluate each test peptide against multiple composition-preserving scrambled sequences using the same target and identical ColabFold settings. The resulting control scores define a conditional null distribution for sequences with the same amino-acid composition but randomized residue order.

This procedure calibrates the score against a clearly defined null. It does **not** convert the score into an experimentally validated probability of binding.

## 1. Null-model definition

For each peptide sequence, generate shuffled controls that preserve:

- peptide length;
- exact amino-acid composition;
- net charge implied by composition at a fixed pH convention;
- mean hydropathy and other composition-derived global properties.

Shuffling intentionally disrupts:

- residue order;
- linear binding motifs;
- local charge patterning;
- hydrophobic clustering;
- amphipathic periodicity;
- local secondary-structure propensity;
- sequence-specific target contacts.

Accordingly, describe these controls as **composition-preserving sequence scrambles**, not as controls that strictly preserve disorder propensity or as guaranteed nonbinders.

## 2. Number of controls

Use the following practical minimums:

- **Development or triage:** at least 20 scrambles per peptide.
- **Routine final analysis:** 50--100 scrambles per peptide.
- **Tail-probability estimation below 0.01:** substantially more than 100 scrambles, or an explicitly fitted and validated null model.

One scramble per peptide is not sufficient to estimate a peptide-specific null mean and variance. A single scramble may be used as a paired diagnostic control, but it does not support a peptide-specific z-score.

## 3. Scramble generation

For each peptide:

1. Randomly permute residue order while preserving the complete residue multiset.
2. Remove duplicates.
3. Reject any shuffle identical to the original sequence.
4. Record the random seed and scrambling algorithm.
5. Optionally flag, rather than silently discard, scrambles that retain a known motif.

For peptides containing many repeated residues, enumerate the number of unique permutations or detect when the requested number of unique scrambles is impossible.

## 4. ColabFold execution controls

Run the original peptide and all of its scrambles under an identical inference protocol. Fix and record:

- ColabFold and AlphaFold model versions;
- database release and MSA-generation settings;
- target sequence and target MSA;
- template settings;
- model set;
- recycle count;
- number of predictions per sequence;
- dropout or sampling settings;
- random-seed policy;
- ranking and model-selection rule;
- score-component definitions and weights.

Cache and reuse the target MSA where supported. Calibration must be regenerated after materially changing the inference protocol or the definition of `composite_score`.

## 5. Model-level aggregation

If multiple structural predictions are generated per peptide, define the aggregation rule before examining the results. Suitable options include:

- maximum score across models;
- median score across models;
- mean of the top *k* models;
- score of the model selected by a fixed ColabFold ranking criterion.

Apply exactly the same aggregation rule to test peptides and scrambles. The maximum is sensitive to the number of sampled models, so model counts must be identical across conditions.

## 6. Reported calibration statistics

For peptide *i*, let the observed score be \(s_i\), and let \(s_{ib}\) denote the score of scramble \(b\), for \(b=1,\ldots,B\).

### Conventional z-score

Use only after checking that the scramble distribution is reasonably unimodal and not strongly skewed:

\[
z_i = \frac{s_i - \bar{s}_{0,i}}{\operatorname{sd}(s_{0,i})}.
\]

### Robust z-score

Prefer this when the null contains occasional high-scoring scrambles:

\[
z_i^{\mathrm{robust}} =
\frac{s_i - \operatorname{median}(s_{0,i})}
{1.4826\,\operatorname{MAD}(s_{0,i})}.
\]

If the median absolute deviation is zero, report the empirical rank and do not assign an infinite robust z-score.

### Empirical one-sided p-value

For a score where larger values are more favorable:

\[
\hat p_i =
\frac{1 + \sum_{b=1}^{B} \mathbf{1}(s_{ib} \ge s_i)}
{B+1}.
\]

The added one in the numerator and denominator prevents zero p-values under random permutation sampling.

### Empirical percentile

Also report the observed score's percentile within the scramble distribution. This is easier to interpret than a z-score when the null distribution is bounded, skewed, or non-Gaussian.

## 7. Required output per peptide

Store at least:

- target identifier;
- original peptide sequence;
- peptide length and composition summary;
- raw `composite_score`;
- number of requested and valid unique scrambles;
- scramble-score mean and standard deviation;
- scramble-score median and MAD;
- conventional z-score, when valid;
- robust z-score;
- empirical percentile;
- empirical p-value;
- ColabFold configuration hash or manifest identifier;
- score-definition version;
- random seeds;
- failure count and failure reasons.

Retain all individual scramble scores rather than only their summary statistics.

## 8. Panel-level analysis

Do not automatically pool one scramble from each peptide into a single null distribution. Pooling assumes that the scramble scores are exchangeable across differences in peptide length, composition, and target.

A pooled or hierarchical panel-level null may be used only after checking whether null-score location and variance depend on:

- target;
- peptide length;
- net charge;
- hydropathy;
- composition;
- disorder-related sequence features;
- model or software version.

When these dependencies are present, use peptide-specific nulls or a stratified/hierarchical calibration model.

## 9. Validation before deployment

Before interpreting calibrated values as evidence of sequence-specific binding, evaluate the procedure on a benchmark containing, where possible:

- experimentally supported binders;
- experimentally supported nonbinders;
- composition-matched scrambles;
- sequence mutants with known gain or loss of binding;
- multiple targets with different structural classes.

Assess:

- enrichment of known binders over their scrambles;
- ROC-AUC and precision--recall performance;
- calibration of empirical p-values under negative controls;
- sensitivity to the number of scrambles;
- sensitivity to model sampling depth;
- stability across ColabFold versions and target classes;
- ablation of each `composite_score` component.

Null calibration removes target- and protocol-specific location and scale effects only to the extent that the controls are generated and scored under the same conditions. It does not rescue an uninformative or poorly weighted underlying score.

## 10. Recommended minimum decision rule

For initial screening, retain the raw score and prioritize peptides that satisfy both:

1. a prespecified minimum structural-confidence criterion; and
2. an empirical scramble percentile or p-value threshold.

Do not use a universal z-score threshold until its operating characteristics have been measured on known binders and nonbinders. Correct empirical p-values for multiple testing when many peptides are screened, for example using the Benjamini--Hochberg false-discovery-rate procedure.

## 11. Suggested implementation hierarchy

### Minimal viable implementation

- 20 unique scrambles per peptide;
- identical ColabFold protocol for native and scrambled sequences;
- raw score, robust z-score, percentile, and empirical p-value;
- complete configuration and seed logging.

### Recommended production implementation

- 50--100 unique scrambles per peptide;
- repeated model sampling under a fixed aggregation rule;
- benchmark-based selection of score components and thresholds;
- multiple-testing correction;
- versioned configuration manifests and retained per-model outputs.

## Interpretation statement

A suitable reporting statement is:

> The calibrated score measures how unusually favorable a peptide's ColabFold-derived `composite_score` is relative to composition-preserving randomized sequences evaluated against the same target under the same inference protocol. It is evidence of sequence-specific model preference, not direct proof of physical binding or affinity.

## Supporting literature

The accompanying BibTeX file contains references supporting three distinct aspects of this implementation:

1. AlphaFold-Multimer confidence metrics contain useful but target- and construct-dependent interaction information.
2. Structural and docking scores can be made more interpretable by comparison with explicit decoy distributions and z-score normalization.
3. Randomly sampled permutation p-values require finite-sampling correction and should not be reported as zero.
