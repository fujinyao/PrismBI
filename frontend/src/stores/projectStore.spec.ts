import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', () => {
  return {
    projectsApi: {
      list: vi.fn(),
      switch: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      delete: vi.fn(),
    },
  }
})

vi.mock('./threadStore', () => ({
  useThreadStore: {
    getState: () => ({
      reset: vi.fn(),
    }),
  },
}))

vi.mock('./modelingStore', () => ({
  useModelingStore: {
    getState: () => ({
      reset: vi.fn(),
    }),
  },
}))

vi.mock('./recommendationStore', () => ({
  useRecommendationStore: {
    getState: () => ({
      reset: vi.fn(),
    }),
  },
}))

import { projectsApi } from '@/lib/api'
import { useProjectStore } from './projectStore'

describe('projectStore deleteProject', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useProjectStore.setState({
      projects: [
        {
          id: 1,
          name: 'project-a',
          language: 'EN',
          version: '1.0',
          is_current: true,
        },
        {
          id: 2,
          name: 'project-b',
          language: 'EN',
          version: '1.0',
          is_current: false,
        },
      ],
      currentProject: {
        id: 1,
        name: 'project-a',
        language: 'EN',
        version: '1.0',
        is_current: true,
      },
      loading: false,
      loaded: true,
      error: null,
    })
  })

  it('optimistically removes deleted project even when fetchProjects fails', async () => {
    vi.mocked(projectsApi.delete).mockResolvedValue({ success: true } as never)
    vi.mocked(projectsApi.list).mockRejectedValue(new Error('network error'))

    await useProjectStore.getState().deleteProject(1)

    const state = useProjectStore.getState()
    expect(state.projects.map((p) => p.id)).toEqual([2])
    expect(state.currentProject).toBeNull()
    expect(state.error).toBe('network error')
  })

  it('rolls back optimistic update when delete request fails', async () => {
    vi.mocked(projectsApi.delete).mockRejectedValue(new Error('delete failed'))

    await expect(useProjectStore.getState().deleteProject(1)).rejects.toThrow('delete failed')

    const state = useProjectStore.getState()
    expect(state.projects.map((p) => p.id)).toEqual([1, 2])
    expect(state.currentProject?.id).toBe(1)
    expect(state.error).toBe('delete failed')
  })
})
