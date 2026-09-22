import { expect, request, test } from '@playwright/test'

const originalPassword = 'correct horse battery staple'
const replacementPassword = 'replacement horse battery staple'

test('production authentication and forced-logout flow', async ({ browser, page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Create the first account' })).toBeVisible()
  await page.getByLabel('Username').fill('learner')
  await page.getByLabel('Password').fill(originalPassword)
  await page.getByLabel('Bootstrap secret').fill('production-bootstrap-token-for-browser-test')
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.getByRole('heading', { name: 'Today' })).toBeVisible()
  const sessionCookie = (await page.context().cookies()).find((item) => item.name === 'lcc_session')
  expect(sessionCookie?.secure).toBe(true)
  expect(sessionCookie?.httpOnly).toBe(true)
  expect(sessionCookie?.sameSite).toBe('Lax')

  await page.getByRole('link', { name: 'Settings' }).click()
  await page.getByLabel('Current password').fill(originalPassword)
  await page.getByLabel('New password', { exact: true }).fill(replacementPassword)
  await page.getByLabel('Confirm new password').fill(replacementPassword)
  await page.getByRole('button', { name: 'Change password' }).click()
  await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible()
  await expect(page.getByText('sudo lcc-admin recover-password')).toBeVisible()

  await page.getByLabel('Username').fill('learner')
  await page.getByLabel('Password').fill(replacementPassword)
  await page.getByRole('button', { name: 'Sign in' }).click()

  const otherContext = await browser.newContext({ ignoreHTTPSErrors: true })
  const otherPage = await otherContext.newPage()
  await otherPage.goto(process.env.LCC_E2E_BASE_URL ?? 'https://localhost:8443')
  await otherPage.getByLabel('Username').fill('learner')
  await otherPage.getByLabel('Password').fill(replacementPassword)
  await otherPage.getByRole('button', { name: 'Sign in' }).click()
  await expect(otherPage.getByRole('heading', { name: 'Today' })).toBeVisible()

  await page.getByRole('link', { name: 'Settings' }).click()
  await page.getByRole('button', { name: 'Revoke other sessions' }).click()
  await expect(page.getByText('All other sessions were revoked.')).toBeVisible()
  await otherPage.reload()
  await expect(otherPage.getByRole('heading', { name: 'Welcome back' })).toBeVisible()
  await otherContext.close()
  await page.getByRole('button', { name: 'Revoke all sessions' }).click()
  await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible()
})

test('production timer survives refresh and completes', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Username').fill('learner')
  await page.getByLabel('Password').fill(replacementPassword)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await page.getByRole('link', { name: 'Activity' }).click()
  await expect(page.getByRole('heading', { name: 'Activity', level: 1 })).toBeVisible()
  await page.getByLabel('Activity title').fill('Production timer')
  await page.getByRole('button', { name: 'Create Activity' }).click()
  await page.getByRole('button', { name: 'Start timer' }).click()
  await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible()
  await page.getByRole('button', { name: 'Pause' }).click()
  await expect(page.getByRole('button', { name: 'Resume' })).toBeVisible()
  await page.getByRole('button', { name: 'Resume' }).click()
  await expect(page.getByRole('button', { name: 'Complete' })).toBeVisible()
  await page.getByRole('button', { name: 'Complete' }).click()
  await expect(page.getByText('Unlinked or legacy Session')).toBeVisible()
})

test('production V2 authority cutover routes the default learning surfaces', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Username').fill('learner')
  await page.getByLabel('Password').fill(replacementPassword)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Today' })).toBeVisible()
  const sessionResponse = await page.request.get('/api/v1/auth/session')
  expect(sessionResponse.ok()).toBe(true)
  const csrf = (await sessionResponse.json()).csrf_token as string
  const headers = {
    'X-CSRF-Token': csrf,
    Origin: process.env.LCC_E2E_BASE_URL ?? 'https://localhost:8443',
  }
  const post = async (path: string, data: object) => {
    const response = await page.request.post(path, { data, headers })
    if (!response.ok()) {
      throw new Error(`${path}: ${await response.text()}`)
    }
    return response.json()
  }

  const scalesResponse = await page.request.get('/api/v2/capability-scales')
  expect(scalesResponse.ok()).toBe(true)
  const technical = (await scalesResponse.json()).find(
    (item: { stableKey: string }) => item.stableKey === 'technical',
  )
  const levels = Object.fromEntries(
    technical.levels.map((item: { stableKey: string; id: string }) => [item.stableKey, item]),
  )

  const competency = await post('/api/v2/competencies', {
    stable_key: 'production.javascript',
    creation_source: 'production-e2e',
  })
  const definition = await post(`/api/v2/competencies/${competency.id}/definitions`, {
    title: 'JavaScript delivery',
    description: 'Production-stack V2 cutover fixture',
    scope: 'One explicit E2E competency',
    scale_stable_key: 'technical',
    scale_version: 'v1',
    dimension_keys: [],
    effective_at: '2026-01-01T00:00:00Z',
    creation_source: 'production-e2e',
    criteria: [{
      stable_key: 'production.javascript.independent',
      level_stable_key: 'independent',
      dimension_key: null,
      requirement_type: 'required',
      demonstration_rule: 'independent_performance',
      description: 'Demonstrate the production authority competency independently.',
    }],
  })
  await post(`/api/v2/competencies/${competency.id}/definitions/${definition.id}/activate`, {
    reason: 'Production E2E authority fixture',
    source: 'production-e2e',
    idempotency_key: 'production-e2e-definition-active',
  })
  const german = await post('/api/v2/competencies', {
    stable_key: 'production.german',
    creation_source: 'production-e2e',
  })
  const germanDefinition = await post(`/api/v2/competencies/${german.id}/definitions`, {
    title: 'German conversation',
    description: 'Actual work chosen instead of the JavaScript suggestion',
    scope: 'Practice a German conversation independently',
    scale_stable_key: 'technical',
    scale_version: 'v1',
    dimension_keys: [],
    effective_at: '2026-01-01T00:00:00Z',
    creation_source: 'production-e2e',
    criteria: [{
      stable_key: 'production.german.independent',
      level_stable_key: 'independent',
      dimension_key: null,
      requirement_type: 'required',
      demonstration_rule: 'independent_performance',
      description: 'Complete the German conversation independently.',
    }],
  })
  await post(`/api/v2/competencies/${german.id}/definitions/${germanDefinition.id}/activate`, {
    reason: 'Production E2E German actuality fixture',
    source: 'production-e2e',
    idempotency_key: 'production-e2e-german-definition-active',
  })
  const profile = await post('/api/v2/target-profiles', {
    stable_key: 'production-authority-profile',
    creation_source: 'production-e2e',
    version: {
      title: 'Production authority profile',
      description: 'Production-stack cutover profile',
      creation_source: 'production-e2e',
      effective_at: '2026-01-01T00:00:00Z',
      domains: [{
        stable_key: 'learning', title: 'Learning', minimum_percent: 100,
        maximum_percent: 100, order_index: 0,
      }],
      targets: [{
        stable_key: 'production-authority-target',
        competency_identity_id: competency.id,
        dimension_key: null,
        domain_stable_key: 'learning',
        scale_stable_key: 'technical',
        scale_version: 'v1',
        target_level_stable_key: 'independent',
        priority: 'core',
      }],
      milestones: [],
      readiness_gates: [],
    },
  })
  await post(`/api/v2/target-profiles/${profile.profileId}/versions/${profile.versionId}/activate`, {
    reason: 'Production E2E authority fixture',
    source: 'production-e2e',
    idempotency_key: 'production-e2e-profile-active',
  })
  const graph = await post('/api/v2/learning-graphs', {
    stable_key: 'production-authority-graph', creation_source: 'production-e2e',
  })
  const graphVersion = await post(`/api/v2/learning-graphs/${graph.id}/versions`, {
    title: 'Production authority graph',
    description: 'Explicit empty graph',
    effective_at: '2026-01-01T00:00:00Z',
    creation_source: 'production-e2e',
    edges: [],
  })
  await post(`/api/v2/learning-graphs/${graph.id}/versions/${graphVersion.id}/activate`, {
    reason: 'Production E2E authority fixture',
    source: 'production-e2e',
    idempotency_key: 'production-e2e-graph-active',
  })
  const curriculum = await post('/api/v2/curricula', {
    stable_key: 'production-authority-curriculum', creation_source: 'production-e2e',
  })
  const curriculumVersion = await post(`/api/v2/curricula/${curriculum.id}/versions`, {
    title: 'Production authority curriculum',
    description: 'One explicit JavaScript learning action',
    effective_at: '2026-01-01T00:00:00Z',
    creation_source: 'production-e2e',
    objectives: [{
      stable_key: 'javascript-practice',
      title: 'JavaScript practice',
      description: 'Practice the target competency.',
      order_index: 0,
    }],
    units: [{
      stable_key: 'javascript-control-loop',
      objective_stable_key: 'javascript-practice',
      kind: 'practice_task',
      title: 'Practice JavaScript control flow',
      description: 'Implement a small deterministic JavaScript exercise.',
      action: {
        kind: 'practice_task',
        instructions: 'Implement and run a small JavaScript control-flow exercise.',
      },
      status: 'active',
      provenance: 'production-e2e',
      order_index: 0,
      minimum_useful_duration_ms: 300000,
      preferred_duration_ms: 600000,
      maximum_useful_duration_ms: 900000,
      targets: [{
        semantic_definition_id: definition.id,
        criterion_definition_id: definition.criteria[0].id,
        scale_version_id: technical.id,
        dimension_id: null,
        intended_learning_outcome: 'Practice JavaScript independently.',
        minimum_level_id: levels.unexposed.id,
        maximum_level_id: levels.independent.id,
        supports_unassessed: true,
        role: 'primary',
        order_index: 0,
      }],
      requirements: [],
      evidence_opportunities: [],
    }],
    assessment_rubrics: [],
  })
  await post(`/api/v2/curricula/${curriculum.id}/versions/${curriculumVersion.id}/activate`, {
    reason: 'Production E2E authority fixture',
    source: 'production-e2e',
    idempotency_key: 'production-e2e-curriculum-active',
  })
  const project = await post('/api/v2/projects', {
    stable_key: 'production-control-loop-project', creation_source: 'production-e2e',
  })
  const projectVersion = await post(`/api/v2/projects/${project.id}/versions`, {
    title: 'Production control-loop project',
    description: 'Canonical Project input without fabricated legacy content.',
    effective_at: '2026-01-01T00:00:00Z',
    creation_source: 'production-e2e',
    goals: [], milestones: [], tasks: [], criteria: [], targets: [], requirements: [],
    dependencies: [], evidence_opportunities: [],
  })
  await post(`/api/v2/projects/${project.id}/versions/${projectVersion.id}/activate`, {
    reason: 'Production E2E Project fixture',
    source: 'production-e2e',
    idempotency_key: 'production-e2e-project-active',
  })
  await post(`/api/v2/projects/${project.id}/events`, {
    event_type: 'project_lifecycle',
    project_lifecycle_state: 'active',
    details: {},
    source: 'production-e2e',
    idempotency_key: 'production-e2e-project-lifecycle',
  })
  expect((await page.request.get('/api/v1/settings/discipline')).ok()).toBe(true)
  await post('/api/v2/roadmap-projection/rebuild', {})
  const analysis = await post('/api/v2/analysis/runs', {
    idempotency_key: 'production-e2e-analysis', purpose: 'learning_control',
  })
  const readiness = await page.request.get('/api/v2/authority/readiness')
  expect(readiness.ok()).toBe(true)
  expect((await readiness.json()).ready).toBe(true)
  await post('/api/v2/authority/activate-v2', {
    idempotency_key: 'production-e2e-authority-active',
    reason: 'Production-stack readiness passed',
  })
  const recommendation = await post('/api/v2/recommendations/runs', {
    idempotency_key: 'production-e2e-recommendation',
    analysis_snapshot_id: analysis.id,
    available_time_ms: null,
    context_costs: [],
  })
  expect(recommendation.portfolio[0].portfolioRole).toBe('primary')
  expect(recommendation.portfolio[0].title).toBe('Practice JavaScript control flow')
  const today = await post('/api/v2/today/generations', {
    idempotency_key: 'production-e2e-today-generation',
    analysis_snapshot_id: analysis.id,
    available_time_ms: null,
    context_costs: [],
  })
  const suggestion = today.suggestions[0]
  expect(suggestion.presentation.title).toBe('Practice JavaScript control flow')
  const currentBefore = await page.request.get('/api/v2/today/current')
  const currentBeforeAgain = await page.request.get('/api/v2/today/current')
  expect(currentBefore.ok()).toBe(true)
  expect(currentBeforeAgain.ok()).toBe(true)
  expect(await currentBeforeAgain.json()).toEqual(await currentBefore.json())

  const germanActivity = await post('/api/v2/activities', {
    title: 'German conversation practice',
    description: 'Actual work chosen by the learner.',
    category_stable_key: 'practice',
    occurred_at: new Date().toISOString(),
    outcome_classification: 'completed',
  })
  const germanSession = await post('/api/v2/sessions/manual', {
    activity_id: germanActivity.id,
    assistance_mode: 'none',
    started_at: new Date().toISOString(),
    duration_ms: 600000,
    difficulty: 3,
    outcome: 'completed',
    notes: 'German was the actual learning work.',
    contributions: [{
      target_type: 'competency',
      competency_identity_id: german.id,
      criterion_identity_id: null,
      relevance: 'primary',
      provenance: 'user_confirmed',
    }],
  })
  expect(germanSession.activityId).toBe(germanActivity.id)
  const replaced = await post(`/api/v2/today/suggestions/${suggestion.id}/replace`, {
    idempotency_key: 'production-e2e-german-replacement',
    activity_id: germanActivity.id,
    replacement_suggestion_id: null,
    reason_code: 'worked_on_something_else',
    feedback: 'German was studied instead.',
  })
  expect(replaced.status).toBe('replaced')
  expect(replaced.activityRelations).toEqual(
    expect.arrayContaining([expect.objectContaining({ activityId: germanActivity.id, type: 'replaced' })]),
  )
  const germanEvidenceResponse = await page.request.get(
    `/api/v2/evidence?competency_identity_id=${german.id}`,
  )
  const javascriptEvidenceResponse = await page.request.get(
    `/api/v2/evidence?competency_identity_id=${competency.id}`,
  )
  expect(germanEvidenceResponse.ok()).toBe(true)
  expect(javascriptEvidenceResponse.ok()).toBe(true)
  expect((await germanEvidenceResponse.json()).items).toEqual(
    expect.arrayContaining([expect.objectContaining({ sourceType: 'learning_session' })]),
  )
  expect((await javascriptEvidenceResponse.json()).items).toEqual([])
  const germanCapability = await page.request.get(`/api/v2/capabilities/${german.id}`)
  expect(germanCapability.ok()).toBe(true)
  expect((await germanCapability.json()).states.length).toBeGreaterThan(0)

  const portableExport = await post('/api/v1/import-export/export', {
    purpose: 'portable_logical_backup', format: 'json',
  })
  expect(portableExport.content.schemaVersion).toBe(9)
  const portableInspection = await post('/api/v1/import-export/import/inspect', {
    filename: 'production-e2e-portable-v9.json', package: portableExport.content,
  })
  expect(portableInspection.valid).toBe(true)
  expect(portableInspection.summary.packageType).toBe('portable_logical_backup')
  expect(portableInspection.summary.replacementRequired).toBe(true)

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Today' })).toBeVisible()
  await page.getByRole('link', { name: 'Roadmap', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Roadmap', exact: true })).toBeVisible()
  await page.goto('/insights/recommendations')
  await expect(page.getByRole('heading', { name: 'Recommendations' })).toBeVisible()
  await page.getByRole('link', { name: 'Profile', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Profile', exact: true })).toBeVisible()
  await page.goto('/insights/legacy/roadmap')
  await expect(page.getByRole('heading', { name: 'Legacy Roadmap' })).toBeVisible()
  const blockedLegacyWrite = await page.request.put('/api/v1/roadmap/current-phase/not-a-phase', {
    headers,
  })
  expect(blockedLegacyWrite.status()).toBe(409)
  expect((await blockedLegacyWrite.json()).error.code).toBe('LEGACY_AUTHORITY_READ_ONLY')
})

test('production host, origin, and docs boundaries fail closed', async () => {
  const baseURL = process.env.LCC_E2E_BASE_URL ?? 'https://localhost:8443'
  const api = await request.newContext({ baseURL, ignoreHTTPSErrors: true })
  expect((await api.get('/api/v1/docs')).status()).toBe(404)
  expect(
    (
      await api.post('/api/v1/auth/login', {
        data: { username: 'learner', password: replacementPassword },
      })
    ).status(),
  ).toBe(403)
  const appPort = process.env.LCC_APP_PORT ?? '8000'
  const backend = await request.newContext({ baseURL: `http://127.0.0.1:${appPort}` })
  expect((await backend.get('/api/v1/health', { headers: { Host: 'attacker.example' } })).status()).toBe(
    400,
  )
  await backend.dispose()
  await api.dispose()
})
