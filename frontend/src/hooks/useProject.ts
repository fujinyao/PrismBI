'use client'

import { useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { projectsApi } from '@/lib/api'
import { useProjectStore } from '@/stores/projectStore'

export function useProjects() {
  const store = useProjectStore()
  const { projects, currentProject, loading, fetchProjects, switchProject } = store
  const queryClient = useQueryClient()

  const projectsQuery = useQuery({
    queryKey: ['projects'],
    queryFn: () => projectsApi.list(),
    staleTime: 60 * 1000,
  })

  useEffect(() => {
    if (projectsQuery.data && projects.length === 0) {
      fetchProjects()
    }
  }, [projectsQuery.data, projects.length, fetchProjects])

  const switchMutation = useMutation({
    mutationFn: (id: number) => switchProject(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['threads'] })
      queryClient.invalidateQueries({ queryKey: ['recommendations'] })
      queryClient.invalidateQueries({ queryKey: ['recommendations-onboarding'] })
      queryClient.invalidateQueries({ queryKey: ['modeling'] })
      queryClient.invalidateQueries({ queryKey: ['dashboards'] })
    },
  })

  const createMutation = useMutation({
    mutationFn: (data: { name: string; display_name?: string; type?: string }) =>
      projectsApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      fetchProjects()
    },
  })

  return {
    projects,
    currentProject,
    isLoading: projectsQuery.isLoading || loading,
    switchProject: switchMutation.mutateAsync,
    createProject: createMutation.mutateAsync,
    isSwitching: switchMutation.isPending,
    isCreating: createMutation.isPending,
  }
}
