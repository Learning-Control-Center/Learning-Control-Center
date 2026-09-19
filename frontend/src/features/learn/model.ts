import type { CurriculumAvailability, CurriculumCatalogUnit } from '../../shared/contracts/productCatalog'

export type Curriculum = { id: string; stableKey: string; activeVersionId: string | null }
export type CurriculumVersion = { id: string; version: number; title: string; description: string; contentHash: string; effectiveAt: string }
export type Catalog = { activeVersionReferences: { curriculumId: string; versionId: string; version: number }[]; units: CurriculumCatalogUnit[]; inputHash: string }

export type LearningGroup = 'available' | 'blocked' | 'unknown'

export function learningGroup(availability: CurriculumAvailability | undefined): LearningGroup {
  if (!availability) return 'unknown'
  if ([availability.availabilityState, availability.readinessState, availability.candidateUsabilityState].includes('not_met')) return 'blocked'
  if (availability.availabilityState === 'met' && availability.candidateUsabilityState === 'met') return 'available'
  return 'unknown'
}
