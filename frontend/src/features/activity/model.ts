import type { CurriculumCatalogUnit, ProjectCatalogCandidateApi } from '../../shared/contracts/productCatalog'
import { adaptProjectCatalogCandidate } from '../../shared/contracts/productCatalog'
import type { ActualWorkReference } from '../../shared/contracts/learningReferences'
import type { RoadmapProjection } from '../../shared/contracts/roadmapProjection'

export type Activity = { id: string; title: string; description: string | null; categoryStableKey: string; occurredAt: string | null; createdAt: string }
export type ReferenceOption = { key: string; label: string; reference: ActualWorkReference | null }

export function referenceOptions(projection: RoadmapProjection | null, curriculumUnits: CurriculumCatalogUnit[], projectItems: ProjectCatalogCandidateApi[]): ReferenceOption[] {
  return [
    { key: 'unlinked', label: 'Unlinked Activity', reference: null },
    ...(projection?.nodes ?? []).map((node) => ({ key: `competency:${node.id}`, label: `Competency · ${node.title}`, reference: { kind: 'competency' as const, competencyIdentityId: node.id, semanticDefinitionId: node.semanticDefinitionId, title: node.title } })),
    ...curriculumUnits.map((unit) => ({ key: `curriculum:${unit.unitDefinitionId}`, label: `Learn · ${unit.title}`, reference: { kind: 'curriculum_unit' as const, curriculumId: unit.curriculumId, unitDefinitionId: unit.unitDefinitionId, semanticDefinitionId: unit.targets?.[0]?.semanticDefinitionId, title: unit.title } })),
    ...projectItems.map(adaptProjectCatalogCandidate).map((task) => ({ key: `project:${task.taskDefinitionId}`, label: `Project · ${task.title}`, reference: { kind: 'project_task' as const, projectId: task.projectId, projectVersionId: task.projectVersionId, taskDefinitionId: task.taskDefinitionId, semanticDefinitionId: task.semanticDefinitionIds[0], title: task.title } })),
  ]
}
