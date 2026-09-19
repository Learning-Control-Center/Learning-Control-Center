import { describe, expect, it } from 'vitest'

import { activityHandoffPath, activityResultPath, parseActivityHandoff } from './learningReferences'

describe('actual-work handoff contract', () => {
  it('round-trips a human-named Project task without exposing ID entry', () => {
    const path = activityHandoffPath({
      origin: 'projects',
      returnTo: '/projects/project-1',
      reference: {
        kind: 'project_task',
        projectId: 'project-1',
        projectVersionId: 'version-1',
        taskDefinitionId: 'task-1',
        title: 'Ship the service',
      },
    })
    const parsed = parseActivityHandoff(new URLSearchParams(path.split('?')[1]))
    expect(parsed?.reference.title).toBe('Ship the service')
    expect(parsed?.returnTo).toBe('/projects/project-1')
  })

  it('rejects an arbitrary return URL and falls back to the allowlisted origin', () => {
    const parsed = parseActivityHandoff(
      new URLSearchParams(
        'origin=learn&returnTo=https%3A%2F%2Fevil.example&kind=curriculum_unit&title=Practice&curriculumId=c1&unitDefinitionId=u1',
      ),
    )
    expect(parsed?.returnTo).toBe('/learn')
  })

  it('returns the selected Activity result to the allowlisted source route', () => {
    const handoff = parseActivityHandoff(new URLSearchParams('origin=roadmap&returnTo=%2Froadmap%3Ffocus%3Dc1&kind=competency&title=Practice&competencyIdentityId=c1'))
    expect(handoff).not.toBeNull()
    expect(activityResultPath(handoff!, 'activity-1')).toBe('/roadmap?focus=c1&activityResult=activity-1&activityResultStatus=selected')
  })
})
