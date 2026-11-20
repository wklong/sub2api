//go:build unit

package admin

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Wei-Shaw/sub2api/internal/service"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

type containerUpdateStub struct {
	systemHandlerUpdateServiceStub
	target service.ContainerUpdateTarget
	starts int
}

func (s *containerUpdateStub) IsContainerUpdate() bool { return true }
func (s *containerUpdateStub) StartContainerUpdate(_ context.Context, target service.ContainerUpdateTarget) (*service.ContainerUpdateStatus, error) {
	s.target = target
	s.starts++
	return &service.ContainerUpdateStatus{Phase: "queued"}, nil
}
func (s *containerUpdateStub) GetContainerUpdateStatus(context.Context) (*service.ContainerUpdateStatus, error) {
	return &service.ContainerUpdateStatus{Phase: "pulling"}, nil
}

func TestContainerHandlerQueuesSelectedImageWithoutBinaryUpdateOrRestart(t *testing.T) {
	gin.SetMode(gin.TestMode)
	stub := &containerUpdateStub{}
	handler := NewSystemHandler(stub, nil)
	router := gin.New()
	router.POST("/update", handler.PerformUpdate)
	router.GET("/status", handler.GetUpdateStatus)
	w := httptest.NewRecorder()
	body := `{"commit":"` + strings.Repeat("a", 40) + `","digest":"sha256:` + strings.Repeat("b", 64) + `"}`
	req := httptest.NewRequest(http.MethodPost, "/update", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	router.ServeHTTP(w, req)
	require.Equal(t, http.StatusOK, w.Code)
	require.Contains(t, w.Body.String(), `"accepted":true`)
	require.Contains(t, w.Body.String(), `"need_restart":false`)
	require.Equal(t, 1, stub.starts)
	require.Equal(t, strings.Repeat("a", 40), stub.target.Commit)
	require.Zero(t, stub.performCall)
	w = httptest.NewRecorder()
	router.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/status", nil))
	require.Equal(t, http.StatusOK, w.Code)
	require.Contains(t, w.Body.String(), "pulling")
}

func TestContainerHandlerRejectsMissingSelection(t *testing.T) {
	gin.SetMode(gin.TestMode)
	stub := &containerUpdateStub{}
	handler := NewSystemHandler(stub, nil)
	router := gin.New()
	router.POST("/update", handler.PerformUpdate)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/update", strings.NewReader(`{}`)))
	require.Equal(t, http.StatusBadRequest, w.Code)
	require.Zero(t, stub.starts)
	require.Zero(t, stub.performCall)
}
