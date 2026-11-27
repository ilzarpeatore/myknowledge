/** Shapes of the studies (publications) API. */

export type Study = {
  id: string; title: string; year: number | null; doi: string | null; journal: string | null;
  authors_text: string | null; abstract: string | null; url: string | null; record_type: string | null;
  study_type: string | null; design: string | null; population: string | null; sample_size: number | null;
  variables: string | null; results: string | null; limitations: string | null;
  methodological_quality: string; quality_notes: string | null; quality_assessed_at: string | null;
  metadata_source: string; metadata_fetched_at: string | null; created_at: string;
  claims: number; sources: { id: string; title: string }[];
};

export type StudyDetail = Study & {
  claim_list: { id: string; text: string; claim_type: string; review_status: string; origin: string; entities: { slug: string; name: string }[] }[];
};
