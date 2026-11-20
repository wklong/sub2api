//go:build unit

package service

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestContainerModeNeverDownloadsUpstreamBinary(t *testing.T) {
	t.Setenv("SUB2API_UPDATE_MODE", "container")
	svc := NewUpdateService(nil, nil, "test", "release")
	require.Error(t, svc.PerformUpdate(context.Background()))
	require.Error(t, svc.Rollback())
	require.Error(t, svc.RollbackToVersion(context.Background(), "1.2.3"))
	versions, err := svc.ListRollbackVersions(context.Background())
	require.NoError(t, err)
	require.Empty(t, versions)
}

func TestContainerModeUnavailableUpdaterReportsWarningWithoutUpstreamFallback(t *testing.T) {
	t.Setenv("SUB2API_UPDATE_MODE", "container")
	t.Setenv("SUB2API_COMMIT", "test-commit")
	svc := NewUpdateService(nil, nil, "test", "release")
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	info, err := svc.CheckUpdate(ctx, true)
	require.NoError(t, err)
	require.Equal(t, "container", info.UpdateMode)
	require.Equal(t, "test-commit", info.CurrentCommit)
	require.NotEmpty(t, info.Warning)
	require.False(t, info.HasUpdate)
}
