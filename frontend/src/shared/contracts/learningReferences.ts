import { paths } from '../navigation/paths'

export type HandoffOrigin = 'profile' | 'roadmap' | 'learn' | 'projects' | 'today'

export type CompetencyReference = {
  kind: 'competency'
  competencyIdentityId: string
  semanticDefinitionId?: string
  title: string
}

export type CurriculumUnitReference = {
  kind: 'curriculum_unit'
  curriculumId: string
  unitDefinitionId: string
  semanticDefinitionId?: string
  title: string
}

export type ProjectTaskReference = {
  kind: 'project_task'
  projectId: string
  projectVersionId?: string
  taskDefinitionId: string
  semanticDefinitionId?: string
  title: string
}

export type ActualWorkReference =
  | CompetencyReference
  | CurriculumUnitReference
  | ProjectTaskReference

export type ActualWorkHandoff = {
  origin: HandoffOrigin
  returnTo: string
  reference: ActualWorkReference
}

const originPaths: Record<HandoffOrigin, string> = {
  profile: paths.profile,
  roadmap: paths.roadmap,
  learn: paths.learn,
  projects: paths.projects,
  today: paths.today,
}

const isOrigin = (value: string | null): value is HandoffOrigin =>
  value !== null && value in originPaths

export function activityHandoffPath(handoff: ActualWorkHandoff) {
  const query = new URLSearchParams({
    origin: handoff.origin,
    returnTo: handoff.returnTo,
    kind: handoff.reference.kind,
    title: handoff.reference.title,
  })
  if (handoff.reference.semanticDefinitionId) {
    query.set('semanticDefinitionId', handoff.reference.semanticDefinitionId)
  }
  if (handoff.reference.kind === 'competency') {
    query.set('competencyIdentityId', handoff.reference.competencyIdentityId)
  } else if (handoff.reference.kind === 'curriculum_unit') {
    query.set('curriculumId', handoff.reference.curriculumId)
    query.set('unitDefinitionId', handoff.reference.unitDefinitionId)
  } else {
    query.set('projectId', handoff.reference.projectId)
    query.set('taskDefinitionId', handoff.reference.taskDefinitionId)
    if (handoff.reference.projectVersionId) {
      query.set('projectVersionId', handoff.reference.projectVersionId)
    }
  }
  return `${paths.activity}?${query.toString()}`
}

export function activityResultPath(handoff: ActualWorkHandoff, activityId: string) {
  const [pathAndQuery, hash = ''] = handoff.returnTo.split('#', 2)
  const [path, queryString = ''] = pathAndQuery.split('?', 2)
  const query = new URLSearchParams(queryString)
  query.set('activityResult', activityId)
  query.set('activityResultStatus', 'selected')
  return `${path}?${query.toString()}${hash ? `#${hash}` : ''}`
}

export function parseActivityHandoff(query: URLSearchParams): ActualWorkHandoff | null {
  const origin = query.get('origin')
  const kind = query.get('kind')
  const title = query.get('title')?.trim()
  if (!isOrigin(origin) || !title) return null

  const requestedReturn = query.get('returnTo')
  const returnTo =
    requestedReturn === originPaths[origin] ||
    requestedReturn?.startsWith(`${originPaths[origin]}?`) ||
    (origin === 'profile' && requestedReturn?.startsWith(`${paths.profile}/competencies/`)) ||
    (origin === 'learn' && requestedReturn?.startsWith(`${paths.learn}/curricula/`)) ||
    (origin === 'projects' && requestedReturn?.startsWith(`${paths.projects}/`))
      ? requestedReturn
      : originPaths[origin]
  const semanticDefinitionId = query.get('semanticDefinitionId') ?? undefined

  if (kind === 'competency') {
    const competencyIdentityId = query.get('competencyIdentityId')
    if (!competencyIdentityId) return null
    return {
      origin,
      returnTo,
      reference: { kind, competencyIdentityId, semanticDefinitionId, title },
    }
  }
  if (kind === 'curriculum_unit') {
    const curriculumId = query.get('curriculumId')
    const unitDefinitionId = query.get('unitDefinitionId')
    if (!curriculumId || !unitDefinitionId) return null
    return {
      origin,
      returnTo,
      reference: { kind, curriculumId, unitDefinitionId, semanticDefinitionId, title },
    }
  }
  if (kind === 'project_task') {
    const projectId = query.get('projectId')
    const taskDefinitionId = query.get('taskDefinitionId')
    if (!projectId || !taskDefinitionId) return null
    return {
      origin,
      returnTo,
      reference: {
        kind,
        projectId,
        projectVersionId: query.get('projectVersionId') ?? undefined,
        taskDefinitionId,
        semanticDefinitionId,
        title,
      },
    }
  }
  return null
}
