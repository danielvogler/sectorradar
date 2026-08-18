/** The shape of the JSON the pipeline exports. */

export interface Tag {
  facet: string;
  value: string;
}

export interface Evidence {
  evidence_url: string;
  evidence_quote: string;
}

export interface Offering extends Evidence {
  label: string;
}

export interface CaseStudy extends Evidence {
  title: string;
  industry: string | null;
  summary: string;
}

export interface ClientRef extends Evidence {
  client_name: string;
  industry: string | null;
  relationship: string;
}

export interface Product extends Evidence {
  name: string;
  kind: string;
  summary: string;
}

export interface Signal {
  signal: string;
  present: number;
}

export interface Mention extends Evidence {
  headline: string;
  outlet: string | null;
  kind: string;
  published_year: number | null;
  url: string;
  /** 1 when the company wrote it about itself. Never counts as coverage. */
  is_self_published: number;
}

/** Measured from markup: deterministic, and only what a site controls itself. */
export interface Seo {
  score: number;
  pages_analysed: number;
  is_unknown: boolean;
  findings: string[];
  schema_types: string[];
  components: Record<string, number>;
  title_length?: number;
  description_length?: number;
  languages_declared?: number;
  median_word_count?: number;
  has_canonical?: boolean;
  has_hreflang?: boolean;
  has_open_graph?: boolean;
  has_viewport?: boolean;
  blocks_indexing?: boolean;
  image_alt_ratio?: number;
}

export interface SearchBenchmark {
  band: string;
  companies: number;
  avg_score: number;
  structured_data: number;
  faq_schema: number;
  local_business: number;
  hreflang: number;
  open_graph: number;
  median_words: number;
}

export interface TractionComponent {
  name: string;
  points: number;
  max_points: number;
  detail: string;
  observed: boolean;
}

export interface Traction {
  points: number;
  confidence: number;
  /** True when nothing could be observed — distinct from a low score. */
  is_unknown: boolean;
  components: TractionComponent[];
}

export interface Company {
  id: number;
  domain: string;
  canonical_name: string;
  legal_name: string | null;
  one_liner: string | null;
  street: string | null;
  postal_code: string | null;
  city: string | null;
  canton: string | null;
  lat: number | null;
  lon: number | null;
  headcount_est: number | null;
  founded_year: number | null;
  tier: number | null;
  tier_rationale: string | null;
  relevance: number | null;
  review_state: string;
  geocode_status: string | null;
  is_own: boolean;
  size_band: string;
  offerings: Offering[];
  case_studies: CaseStudy[];
  clients: ClientRef[];
  products: Product[];
  mentions: Mention[];
  traction: Traction;
  seo: Seo;
  attributes: Record<string, string[]>;
  tags: Tag[];
  signals: Signal[];
}

export interface Count {
  label: string;
  n: number;
  share: number;
}

export interface IndustryCoverage {
  industry: string;
  providers: number;
  with_evidence: number;
  share: number;
}

export interface SignalByBand {
  signal: string;
  by_band: Record<string, number>;
  overall: number;
}

export interface Analytics {
  segment: string;
  companies: number;
  compared: number;
  own: number;
  by_tier: Count[];
  by_canton: Count[];
  by_size: Count[];
  services: Count[];
  technologies: Count[];
  industries: IndustryCoverage[];
  signals: SignalByBand[];
  search: SearchBenchmark[];
  stack: Record<string, Count[]>;
  totals: Record<string, number>;
}

/** The question the dataset answers, carried alongside the answer. */
export interface SegmentConfig {
  slug: string;
  name: string;
  country: string;
  inclusion: string;
  tiers: Record<string, string>;
  enrich_tiers: number[];
  facets: Record<string, string[]>;
  sources_enabled: string[];
  queries: string[];
  gold_set_size: number;
}

export interface Dataset {
  generated_at: string;
  /** When the crawl and extraction last ran, as against when the page was built. */
  collected_at: string | null;
  segment: SegmentConfig;
  companies: Company[];
  analytics: Analytics;
}
