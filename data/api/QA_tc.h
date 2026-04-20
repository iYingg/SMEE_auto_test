#ifndef QA_TC_H
#define QA_TC_H

#include "../common/QA_common_types.h"

#ifdef __cplusplus
extern "C" {
#endif

// 一些中文注释
SMEE_INT32 QA4A_request_align_periodic(
    IN SS800_CHUCK_ID_ENUM chunk_id,
    IN const QA4A_ALIGN_SCAN_BASE_STRUCT *align_scan_base,
    IN const QA4A_ALIGN_SCAN_PERIODIC_STRUCT *align_scan_periodic,
    OUT SMEE_INT32 *align_scan_periodic_id);

SMEE_INT32 QA4A_oracle_align_periodic(
    IN SS800_CHUCK_ID_ENUM chunk_id,
    IN const QA4A_ALIGN_SCAN_BASE_STRUCT *align_scan_base,
    IN const QA4A_ALIGN_SCAN_PERIODIC_STRUCT *align_scan_periodic,
    OUT SMEE_INT32 *expected_align_scan_periodic_id);

#ifdef __cplusplus
}
#endif

#endif
