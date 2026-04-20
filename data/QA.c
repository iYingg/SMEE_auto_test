
#include "QA_tc.h"

SMEE_INT32 QA4A_request_align_periodic(
    IN SS800_CHUCK_ID_ENUM chunk_id,
    IN const QA4A_ALIGN_SCAN_BASE_STRUCT *align_scan_base,
    IN const QA4A_ALIGN_SCAN_PERIODIC_STRUCT *align_scan_periodic,
    OUT SMEE_INT32 *align_scan_periodic_id)
{
    if (align_scan_base == 0 || align_scan_periodic == 0 || align_scan_periodic_id == 0) {
        return -1;
    }
    if (chunk_id <= 0 || chunk_id >= SS800_CHUCK_ID_MAX) {
        return -2;
    }

    *align_scan_periodic_id = (SMEE_INT32)(
        ((int)chunk_id * 1000) +
        (align_scan_base->a * 100) +
        (align_scan_periodic->b * 10) +
        ((align_scan_periodic->other_struct[0].some_field +
          align_scan_periodic->other_struct[1].some_field) %
         10));

    return 0;
}
