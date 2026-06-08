import { create } from 'zustand'
import { projectsApi } from '@/lib/api'
import { useThreadStore } from './threadStore'
import { useModelingStore } from './modelingStore'
import { useRecommendationStore } from './recommendationStore'

interface Project {
  id: number
  name: string
  display_name?: string
  description?: string
  prompt?: string
  type?: string
  language: string
  version: string
  is_current: boolean
  created_at?: string
  updated_at?: string
  datasource_count?: number
  member_count?: number
}

interface ProjectState {
  projects: Project[]
  currentProject: Project | null
  loading: boolean
  loaded: boolean
  error: string | null
  fetchProjects: () => Promise<void>
  switchProject: (id: number) => Promise<void>
  createProject: (data: { name: string; display_name?: string; description?: string; prompt?: string; type?: string }) => Promise<number>
  updateProject: (id: number, data: Partial<Project>) => Promise<void>
  deleteProject: (id: number) => Promise<void>
  setCurrentProject: (project: Project | null) => void
  clearError: () => void
}

export const useProjectStore = create<ProjectState>()((set, get) => ({
  projects: [],
  currentProject: null,
  loading: false,
  loaded: false,
  error: null,

  fetchProjects: async () => {
    set({ loading: true, error: null })
    try {
      const res = await projectsApi.list() as Project[] | { items?: Project[] }
      const projects = Array.isArray(res) ? res : (res.items ?? [])
      const current = projects.find((p) => p.is_current) || null
      set({ projects, currentProject: current, loading: false, loaded: true })
    } catch (e) {
      set({ loading: false, loaded: true, error: e instanceof Error ? e.message : 'Failed to fetch projects' })
    }
  },

  clearError: () => set({ error: null }),

  switchProject: async (id: number) => {
    set({ loading: true, error: null })
    try {
      await projectsApi.switch(id)
      useThreadStore.getState().reset()
      useModelingStore.getState().reset()
      useRecommendationStore.getState().reset()
      let { projects } = get()
      if (!projects.some((p) => p.id === id)) {
        await get().fetchProjects()
        projects = get().projects
      }
      const foundProject = projects.find((p) => p.id === id) || null
      if (!foundProject) {
        set({ loading: false, currentProject: null })
        return
      }
      set({
        currentProject: { ...foundProject, is_current: true },
        projects: projects.map((p) => ({ ...p, is_current: p.id === id })),
        loading: false,
      })
    } catch (e) {
      set({ loading: false, error: e instanceof Error ? e.message : 'Failed to switch project' })
    }
  },

  createProject: async (data) => {
    try {
      const res = await projectsApi.create(data)
      await get().fetchProjects()
      return res.id
    } catch (e) {
      set({ error: e instanceof Error ? e.message : 'Failed to create project' })
      throw e
    }
  },

  updateProject: async (id: number, data: Partial<Project>) => {
    try {
      await projectsApi.update(id, data)
      await get().fetchProjects()
    } catch (e) {
      set({ error: e instanceof Error ? e.message : 'Failed to update project' })
      throw e
    }
  },

  deleteProject: async (id: number) => {
    const prevProjects = get().projects
    const prevCurrentProject = get().currentProject
    set((state) => ({
      projects: state.projects.filter((p) => p.id !== id),
      currentProject: state.currentProject?.id === id ? null : state.currentProject,
      error: null,
    }))
    try {
      await projectsApi.delete(id)
      await get().fetchProjects()
    } catch (e) {
      set({
        projects: prevProjects,
        currentProject: prevCurrentProject,
        error: e instanceof Error ? e.message : 'Failed to delete project',
      })
      throw e
    }
  },

  setCurrentProject: (project: Project | null) => {
    set((state) => ({
      currentProject: project,
      projects: state.projects.map((p) => ({ ...p, is_current: Boolean(project && p.id === project.id) })),
    }))
  },
}))
