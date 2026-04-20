#ifndef QA_COMMON_TYPES_H
#define QA_COMMON_TYPES_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define IN
#define OUT
#define INOUT
#define num_smee 3

typedef int32_t SMEE_INT32;

typedef enum {
    SS800_CHUCK_1 = 0,
    SS800_CHUCK_2,
    SS800_CHUCK_ID_MAX
} SS800_CHUCK_ID_ENUM;

typedef struct {
    int some_field;
} OTHER_STRUCT;

typedef struct {
    int a;
} QA4A_ALIGN_SCAN_BASE_STRUCT;

typedef struct {
    int b;
    OTHER_STRUCT other_struct[num_smee];
} QA4A_ALIGN_SCAN_PERIODIC_STRUCT;

#ifdef __cplusplus
}
#endif

#endif
